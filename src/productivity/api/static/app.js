const $ = (id) => document.getElementById(id);
const charts = {};
const WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"];
const C1 = "#2563eb", C2 = "#f59e0b";
const PALETTE = ["#2563eb", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#06b6d4", "#ec4899", "#84cc16", "#64748b"];

// toISOString 은 UTC 기준이라 한국 시간 오전에 하루 전 날짜가 되므로 로컬 날짜로 만든다.
const fmtDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hours = (min) => (min / 60).toFixed(1);

function setRange(days) {
  const end = new Date();
  const start = new Date(end);
  start.setDate(end.getDate() - (days - 1));
  $("start").value = fmtDate(start);
  $("end").value = fmtDate(end);
}

async function api(path, extra = "") {
  const qs = `start=${$("start").value}&end=${$("end").value}${extra}`;
  const res = await fetch(`/api/${path}?${qs}`);
  if (!res.ok) throw new Error(`${path}: ${res.status} ${await res.text()}`);
  return res.json();
}

function draw(id, config) {
  charts[id]?.destroy();
  charts[id] = new Chart($(id), config);
}

const dualAxis = {
  responsive: true,
  interaction: { mode: "index", intersect: false },
  scales: {
    y: { position: "left", title: { display: true, text: "작업 시간(분)" }, beginAtZero: true },
    y1: { position: "right", title: { display: true, text: "커밋 수" }, beginAtZero: true, grid: { drawOnChartArea: false } },
  },
};

function barLine(labels, rows) {
  return {
    data: {
      labels,
      datasets: [
        { type: "bar", label: "추정 작업 시간(분)", data: rows.map((r) => r.work_min), backgroundColor: C1, yAxisID: "y" },
        { type: "line", label: "커밋 수", data: rows.map((r) => r.commits), borderColor: C2, backgroundColor: C2, yAxisID: "y1", tension: 0.3 },
      ],
    },
    options: dualAxis,
  };
}

function renderKpis(s) {
  const items = [
    ["커밋", s.commits, `${s.commit_days}일 커밋 · ${s.repos}개 저장소`],
    ["추정 작업 시간", `${s.work_hours}h`, `일평균 ${s.avg_work_hours_per_day ?? 0}h (${s.work_days}일)`],
    ["시간당 커밋", s.commits_per_hour ?? "-", "커밋 수 ÷ 추정 작업 시간"],
    ["변경 라인", `+${s.insertions} / -${s.deletions}`, "머지 커밋 제외"],
    ["최다 작업 프로젝트", s.top_project ?? "-", "추정 작업 시간 기준"],
  ];
  $("kpis").innerHTML = items
    .map(([l, v, sub]) => `<div class="kpi"><div class="label">${l}</div><div class="value">${esc(v)}</div><div class="sub">${esc(sub)}</div></div>`)
    .join("");
}

function renderWow(w) {
  const names = { commits: "커밋 수", work_hours: "추정 작업 시간(h)", insertions: "추가 라인", deletions: "삭제 라인", work_days: "작업한 날" };
  $("wow").innerHTML = Object.entries(names)
    .map(([k, name]) => {
      const m = w.metrics[k];
      const pct = m.change_pct;
      const cls = pct == null ? "flat" : pct > 0 ? "up" : pct < 0 ? "down" : "flat";
      const pctText = pct == null ? "비교 불가" : `${pct > 0 ? "▲" : pct < 0 ? "▼" : ""} ${Math.abs(pct)}%`;
      return `<div class="item"><div class="name">${name}</div>
        <div class="nums">${m.this_week} <small class="flat">/ 지난주 ${m.last_week}</small></div>
        <div class="${cls}">${pctText}</div></div>`;
    })
    .join("");
}

function renderHeatmap(rows) {
  const max = Math.max(1, ...rows.map((r) => r.work_min));
  let html = `<div></div>` + [...Array(24).keys()].map((h) => `<div class="axis">${h % 3 === 0 ? h : ""}</div>`).join("");
  for (let wd = 0; wd < 7; wd++) {
    html += `<div class="axis">${WEEKDAYS[wd]}</div>`;
    for (let h = 0; h < 24; h++) {
      const r = rows[wd * 24 + h];
      const a = r.work_min / max;
      html += `<div class="cell" title="${WEEKDAYS[wd]} ${h}시: ${r.work_min}분, 커밋 ${r.commits}"
        style="background: rgba(37,99,235,${a === 0 ? 0.06 : 0.15 + 0.85 * a})"></div>`;
    }
  }
  $("heatmap").innerHTML = html;
}

function table(id, headers, rows, emptyText) {
  if (!rows.length) {
    $(id).innerHTML = `<tr><td class="empty">${emptyText}</td></tr>`;
    return;
  }
  const th = headers.map(([label, , num]) => `<th class="${num ? "num" : ""}">${label}</th>`).join("");
  const body = rows
    .map((r) => "<tr>" + headers.map(([, fn, num, cls]) => `<td class="${num ? "num" : ""} ${cls ?? ""}">${esc(fn(r))}</td>`).join("") + "</tr>")
    .join("");
  $(id).innerHTML = `<thead><tr>${th}</tr></thead><tbody>${body}</tbody>`;
}

async function load() {
  try {
    const [summary, daily, hourly, weekday, heatmap, projects, commits, wow] = await Promise.all([
      api("summary"), api("daily"), api("hourly"), api("weekday"), api("heatmap"),
      api("projects"), api("commits"), fetch(`/api/week-over-week?ref=${$("end").value}`).then((r) => r.json()),
    ]);
    renderKpis(summary);
    renderWow(wow);
    draw("dailyChart", barLine(daily.map((r) => r.date.slice(5)), daily));
    draw("hourlyChart", barLine(hourly.map((r) => `${r.hour}시`), hourly));
    draw("weekdayChart", barLine(weekday.map((r) => r.label), weekday));
    renderHeatmap(heatmap);

    const withTime = projects.filter((p) => p.work_min > 0);
    draw("projectChart", {
      type: "doughnut",
      data: {
        labels: withTime.map((p) => p.project),
        datasets: [{ data: withTime.map((p) => p.work_min), backgroundColor: PALETTE }],
      },
      options: { plugins: { legend: { position: "right" } } },
    });

    table("projectTable", [
      ["프로젝트", (r) => r.project],
      ["작업(h)", (r) => hours(r.work_min), true],
      ["비중", (r) => (r.work_share_pct ?? 0) + "%", true],
      ["커밋", (r) => r.commits, true],
      ["변경 라인", (r) => r.changed_lines, true],
    ], projects, "데이터 없음");
    table("commitTable", [
      ["시각", (r) => new Date(r.authored_at).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })],
      ["저장소", (r) => r.repo],
      ["메시지", (r) => r.message, false, "msg"],
      ["+/-", (r) => `+${r.insertions}/-${r.deletions}`, true],
    ], commits, "커밋 없음");
  } catch (e) {
    console.error(e);
    alert("데이터를 불러오지 못했습니다: " + e.message);
  }
}

document.querySelectorAll(".presets button").forEach((b) =>
  b.addEventListener("click", () => { setRange(Number(b.dataset.days)); load(); }));
$("range").addEventListener("submit", (e) => { e.preventDefault(); load(); });
fetch("/api/config").then((r) => r.json()).then((c) => {
  $("gapMin").textContent = c.work_max_gap_min;
  $("firstMin").textContent = c.work_first_commit_min;
});
setRange(30);
load();

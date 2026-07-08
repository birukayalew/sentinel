const VISA_LABELS = {
  sponsors: "✅ Sponsors visa",
  no_sponsorship: "❌ No sponsorship",
  citizens_only: "🛑 Citizens only / clearance",
  unknown: "❓ Visa unstated",
};

const APPLICATION_WEIGHT_LABELS = {
  quick_apply: "Quick apply",
  essay_heavy: "Essay-heavy",
};

const WORKPLACE_LABELS = {
  remote: "Remote",
  hybrid: "Hybrid",
  onsite: "Onsite",
};

function referenceDate(job) {
  return job.posted_at ? new Date(job.posted_at) : new Date(job.first_seen);
}

function daysAgo(date) {
  const ms = Date.now() - date.getTime();
  return Math.max(0, Math.floor(ms / 86400000));
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function freshnessLabel(job) {
  const age = daysAgo(referenceDate(job));
  if (age === 0) return { text: "New today", isNew: true };
  if (age === 1) return { text: "Posted 1 day ago", isNew: false };
  return { text: `Posted ${age} days ago`, isNew: false };
}

function isDeadlineSoon(deadlineText) {
  const parsed = new Date(deadlineText);
  if (isNaN(parsed.getTime())) return false;
  const daysUntil = (parsed.getTime() - Date.now()) / 86400000;
  return daysUntil >= 0 && daysUntil <= 5;
}

function buildBadges(job) {
  const badges = [];

  badges.push({ text: job.cycle_year === 2027 ? "2027" : "❓ Cycle unclear" });

  if (job.deadline_badge) {
    const soon = isDeadlineSoon(job.deadline_badge);
    badges.push({
      text: soon ? `⏰ Closes soon: ${job.deadline_badge}` : `Apply by ${job.deadline_badge}`,
      warn: soon,
    });
  }

  if (job.workplace_badge && WORKPLACE_LABELS[job.workplace_badge]) {
    badges.push({ text: WORKPLACE_LABELS[job.workplace_badge] });
  }

  if (job.level_badge) {
    badges.push({ text: job.level_badge });
  }

  badges.push({ text: VISA_LABELS[job.visa_badge] || VISA_LABELS.unknown });

  if (job.application_weight && APPLICATION_WEIGHT_LABELS[job.application_weight]) {
    badges.push({ text: APPLICATION_WEIGHT_LABELS[job.application_weight] });
  }

  if (typeof job.match_score === "number") {
    badges.push({ text: `${job.match_score}% match` });
  }

  return badges;
}

function renderCard(job) {
  const fresh = freshnessLabel(job);
  const badges = buildBadges(job);

  const badgeHtml = badges
    .map((b) => `<span class="badge${b.warn ? " warn" : ""}">${escapeHtml(b.text)}</span>`)
    .join("");

  return `
    <article class="card">
      <div class="card-top">
        <div>
          <h2 class="card-title">
            <a href="${escapeHtml(job.apply_url)}" target="_blank" rel="noopener">
              ${escapeHtml(job.title)}
            </a>
          </h2>
          <p class="card-company">${escapeHtml(job.company)}${job.location ? " · " + escapeHtml(job.location) : ""}</p>
        </div>
        <span class="card-freshness${fresh.isNew ? " new" : ""}">${fresh.text}</span>
      </div>
      <div class="badges">${badgeHtml}</div>
    </article>
  `;
}

async function main() {
  const listEl = document.getElementById("job-list");
  const statusEl = document.getElementById("status");

  listEl.innerHTML = '<p class="loading">Loading jobs…</p>';

  let jobs;
  try {
    const resp = await fetch("data/jobs.json", { cache: "no-store" });
    jobs = await resp.json();
  } catch (err) {
    listEl.innerHTML = '<p class="empty">Could not load job data.</p>';
    return;
  }

  jobs.sort((a, b) => referenceDate(b) - referenceDate(a));

  statusEl.textContent = `${jobs.length} live listing${jobs.length === 1 ? "" : "s"}`;

  if (jobs.length === 0) {
    listEl.innerHTML = '<p class="empty">No live listings right now. Check back soon.</p>';
    return;
  }

  listEl.innerHTML = jobs.map(renderCard).join("");
}

main();

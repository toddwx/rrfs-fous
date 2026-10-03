const bulletin = document.querySelector("#bulletin");
const cycleSummary = document.querySelector("#cycle-summary");
const updatedTime = document.querySelector("#updated-time");
const fieldNote = document.querySelector("#field-note");
const copyButton = document.querySelector("#copy-button");
const namSummary = document.querySelector("#nam-summary");

async function loadNAMComparison() {
  try {
    const response = await fetch("data/nam_comparison.json", { cache: "no-store" });
    if (!response.ok) throw new Error("Comparison status is not available yet.");
    const report = await response.json();
    if (!report.available) {
      namSummary.textContent = report.message || `No NAM candidate was available for ${report.cycle || "the latest check"}.`;
      return;
    }
    if (!report.comparisonAvailable) {
      namSummary.textContent = `NAM data for ${report.cycle} were collected, but a matching official FOUS bulletin was not available for comparison.`;
      return;
    }
    const scores = report.summaries || {};
    const temps = ["T1", "T3", "T5"].filter((field) => scores[field]?.samples)
      .map((field) => `${field}: within 1°C in ${scores[field].within1C}/${scores[field].samples}`).join(" · ");
    const ptt = scores.PTT;
    let rain = "PTT: no matched periods yet";
    if (ptt?.status?.startsWith("unavailable")) {
      rain = "PTT: NAM precipitation data unavailable for this check";
    } else if (ptt?.wetPeriods) {
      const max = ptt.largestWetDifferenceHundredthsIn == null ? "unknown" : `${(ptt.largestWetDifferenceHundredthsIn / 100).toFixed(2)} in`;
      const largest = ptt.largestWetPeriod;
      const largestNote = largest
        ? ` at ${largest.station} ${largest.forecastHour}h (NAM ${ (largest.candidateHundredthsIn / 100).toFixed(2) } / FOUS ${ (largest.officialHundredthsIn / 100).toFixed(2) } in)`
        : "";
      const early = (ptt.earlyWetPeriods || []).find((period) =>
        Math.max(period.candidateHundredthsIn, period.officialHundredthsIn) >= 10);
      const earlyNote = early
        ? `; early rain ${early.station} ${early.forecastHour}h: NAM ${(early.candidateHundredthsIn / 100).toFixed(2)} / FOUS ${(early.officialHundredthsIn / 100).toFixed(2)} in`
        : "";
      rain = `PTT: ${ptt.wetWithin010In}/${ptt.wetPeriods} wet periods within 0.10 in; largest difference ${max}${largestNote}${earlyNote}`;
      if (ptt.status?.startsWith("partial")) rain += " (some intervals unavailable)";
    } else if (ptt?.status?.startsWith("inconclusive")) {
      rain = "PTT: no matched six-hour amount reached 0.10 in; more rain is needed to judge the comparison";
    } else if (ptt?.periodsCompared && ptt.status?.startsWith("unavailable")) {
      rain = "PTT: NAM precipitation intervals were missing for this check";
    }
    const exploratory = report.additionalFieldSummaries || {};
    const extra = [];
    for (const field of ["DD", "FF", "PS", "HH", "LI", "R1", "R2", "R3", "VVV"]) {
      const score = exploratory[field];
      if (!score?.samples) continue;
      if (field === "DD") extra.push(`DD within 20° ${score.within20deg}/${score.samples}`);
      else extra.push(`${field} exact ${score.exact}/${score.samples}`);
    }
    const expanded = extra.length
      ? `Other fields (rough comparisons): ${extra.join(" · ")}. Humidity and vertical-motion recipes still need work.`
      : "Other field comparisons are being collected.";
    namSummary.textContent = [`${report.cycle}`, temps || "Temperature comparison unavailable", rain, expanded].join(" · ");
  } catch (error) {
    namSummary.textContent = error.message;
  }
}

async function loadForecast() {
  try {
    const [statusResponse, textResponse, stationsResponse] = await Promise.all([
      fetch("data/status.json", { cache: "no-store" }),
      fetch("data/latest.txt", { cache: "no-store" }),
      fetch("data/stations.json", { cache: "no-store" }),
    ]);
    if (!statusResponse.ok || !textResponse.ok || !stationsResponse.ok) throw new Error("Forecast files are not available yet.");
    const [status, text, stationData] = await Promise.all([
      statusResponse.json(), textResponse.text(), stationsResponse.json(),
    ]);
    bulletin.textContent = text.trimEnd();
    cycleSummary.textContent = `${status.model} ${status.cycle} · Forecast through hour ${status.forecastThroughHour}`;
    updatedTime.textContent = `Updated ${new Date(status.updatedAt).toLocaleString()}`;
    fieldNote.textContent = status.note;
    document.querySelector("#station-summary").textContent = `Stations: ${stationData.stations.map((station) => station.code).join(" · ")}`;
  } catch (error) {
    bulletin.textContent = "The latest forecast could not be loaded. Please try again later.";
    cycleSummary.textContent = "Forecast update unavailable.";
    updatedTime.textContent = error.message;
  }
}

copyButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(bulletin.textContent);
    copyButton.textContent = "Copied";
    window.setTimeout(() => { copyButton.textContent = "Copy forecast"; }, 1800);
  } catch {
    copyButton.textContent = "Select text to copy";
  }
});

loadForecast();
loadNAMComparison();

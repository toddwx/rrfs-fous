const bulletin = document.querySelector("#bulletin");
const cycleSummary = document.querySelector("#cycle-summary");
const updatedTime = document.querySelector("#updated-time");
const fieldNote = document.querySelector("#field-note");
const copyButton = document.querySelector("#copy-button");

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

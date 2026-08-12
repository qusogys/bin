const form = document.getElementById('searchForm');
const queryInput = document.getElementById('query');
const infoDiv = document.getElementById('info');
const daysSelect = document.getElementById('days');
const dailyDiv = document.getElementById('daily');
const tempChartCtx = document.getElementById('tempChart').getContext('2d');

let tempChart = null;

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = queryInput.value.trim();
  const days = daysSelect.value;
  if (!q) return;
  await fetchWeather(q, days);
});

async function fetchWeather(q, days){
  infoDiv.classList.add('hidden');
  dailyDiv.innerHTML = '';
  document.getElementById('tempChart').classList.add('hidden');

  try {
    const res = await fetch(`/api/weather?${new URLSearchParams({ q, days })}`);
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || 'API error');
      return;
    }
    const data = await res.json();
    showInfo(data);
  } catch (err) {
    alert('Network error: ' + err.message);
  }
}

function showInfo(data){
  infoDiv.classList.remove('hidden');
  document.getElementById('tempChart').classList.remove('hidden');

  const loc = data.location;
  const cur = data.current;
  const daily = data.daily;

  infoDiv.innerHTML = `
    <h2>${loc}</h2>
    <div class="small">Lat: ${parseFloat(data.latitude).toFixed(4)}, Lon: ${parseFloat(data.longitude).toFixed(4)}</div>
    ${cur ? `<p>Current: ${cur.temperature}°C, wind ${cur.windspeed} m/s (time ${cur.time})</p>` : ''}
  `;

  // build chart for daily temps
  const labels = daily.time || [];
  const max = daily.temperature_2m_max || [];
  const min = daily.temperature_2m_min || [];

  if (tempChart) tempChart.destroy();
  tempChart = new Chart(tempChartCtx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Max (°C)', data: max, borderColor: '#e11d48', backgroundColor: 'rgba(225,29,72,0.08)', tension:0.3 },
        { label: 'Min (°C)', data: min, borderColor: '#0ea5e9', backgroundColor: 'rgba(14,165,233,0.08)', tension:0.3 }
      ]
    },
    options: { responsive:true, maintainAspectRatio:false, scales:{ x:{ ticks:{maxRotation:45,minRotation:0} } } }
  });

  // daily cards
  dailyDiv.innerHTML = '';
  for (let i = 0; i < labels.length; i++){
    const date = labels[i];
    const tmax = max[i];
    const tmin = min[i];
    const precip = (daily.precipitation_sum && daily.precipitation_sum[i] !== undefined) ? daily.precipitation_sum[i] : '—';
    const el = document.createElement('div');
    el.className = 'daily-item';
    el.innerHTML = `<div><strong>${date}</strong></div>
                    <div class="small">Max: ${tmax}°C</div>
                    <div class="small">Min: ${tmin}°C</div>
                    <div class="small">Precip: ${precip}</div>`;
    dailyDiv.appendChild(el);
  }
}

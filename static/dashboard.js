/**
 * SENTINEL-X — Phase 4/5 Industrial Telemetry Dashboard
 * Real-time WebSocket/Polling hardware monitor, Chart.js integrations,
 * motion classification, MQ-135 Gas telemetry, and Emergency SOS alerting.
 */

document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Elements ---
    const emergencyBanner = document.getElementById('emergencyBanner');
    const usbStatusBadge = document.getElementById('usbStatusBadge');
    const usbStatusText = document.getElementById('usbStatusText');
    const activePortText = document.getElementById('activePortText');
    const uptimeDisplay = document.getElementById('uptimeDisplay');
    const lastSeenDisplay = document.getElementById('lastSeenDisplay');

    const portSelect = document.getElementById('portSelect');
    const baudSelect = document.getElementById('baudSelect');
    const btnRefreshPorts = document.getElementById('btnRefreshPorts');
    const btnApplyConfig = document.getElementById('btnApplyConfig');

    const motionStatusBadge = document.getElementById('motionStatusBadge');
    const motionIcon = document.getElementById('motionIcon');
    const motionLabel = document.getElementById('motionLabel');

    // Sensor Cards
    const tempVal = document.getElementById('tempVal');
    const tempGauge = document.getElementById('tempGauge');
    const tempStatus = document.getElementById('tempStatus');
    const dhtTempBadge = document.getElementById('dhtTempBadge');

    const humVal = document.getElementById('humVal');
    const humGauge = document.getElementById('humGauge');
    const humStatus = document.getElementById('humStatus');
    const dhtHumBadge = document.getElementById('dhtHumBadge');

    const magVal = document.getElementById('magVal');
    const magGauge = document.getElementById('magGauge');
    const motionSubText = document.getElementById('motionSubText');
    const stateChip = document.getElementById('stateChip');
    const mpuStatusBadge = document.getElementById('mpuStatusBadge');

    // Vector Displays
    const axVal = document.getElementById('axVal');
    const ayVal = document.getElementById('ayVal');
    const azVal = document.getElementById('azVal');
    const gxVal = document.getElementById('gxVal');
    const gyVal = document.getElementById('gyVal');
    const gzVal = document.getElementById('gzVal');

    // Gas & Emergency Sensors
    const gasVal = document.getElementById('gasVal');
    const gasGauge = document.getElementById('gasGauge');
    const gasVoltageText = document.getElementById('gasVoltageText');
    const gasStatusChip = document.getElementById('gasStatusChip');

    const cardEmergency = document.getElementById('cardEmergency');
    const sosIcon = document.getElementById('sosIcon');
    const sosStatusText = document.getElementById('sosStatusText');
    const sosChip = document.getElementById('sosChip');

    // Logs & Terminal
    const terminalStream = document.getElementById('terminalStream');
    const btnClearLogs = document.getElementById('btnClearLogs');
    const logCounter = document.getElementById('logCounter');

    // --- State & Constants ---
    const STANDARD_GRAVITY = 9.80665; // m/s^2 for 1.0g
    const MAX_CHART_POINTS = 30;
    let lastPacketTime = null;
    let pollInterval = null;
    let totalLogsCount = 0;

    /**
     * Motion Classification Algorithm:
     * Evaluates net dynamic acceleration: Delta = |Mag_g - 1.0g|
     * - Delta < 0.08g : STATIONARY (worker at rest / nominal)
     * - 0.08g <= Delta < 0.35g : ACTIVE MOTION (walking / normal manual activity)
     * - Delta >= 0.35g : HIGH DYNAMICS / IMPACT WARNING (sudden jerk, stumble, or shock)
     */
    const MOTION_THRESHOLDS = {
        STATIONARY_MAX: 0.08,
        ACTIVE_MAX: 0.35
    };

    function classifyMotion(mag_g) {
        const delta = Math.abs(mag_g - 1.0);
        if (delta < MOTION_THRESHOLDS.STATIONARY_MAX) {
            return { state: 'STATIONARY', level: 'nominal', delta: delta.toFixed(3) };
        } else if (delta < MOTION_THRESHOLDS.ACTIVE_MAX) {
            return { state: 'ACTIVE MOTION', level: 'active', delta: delta.toFixed(3) };
        } else {
            return { state: 'IMPACT / HIGH MOTION', level: 'alert', delta: delta.toFixed(3) };
        }
    }

    // --- Format Uptime (ms to HH:MM:SS) ---
    function formatUptime(uptimeMs) {
        if (!uptimeMs && uptimeMs !== 0) return '--:--:--';
        const totalSeconds = Math.floor(uptimeMs / 1000);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;
        return [
            String(hours).padStart(2, '0'),
            String(minutes).padStart(2, '0'),
            String(seconds).padStart(2, '0')
        ].join(':');
    }

    // --- Initialize Chart.js ---
    const chartDefaults = {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 200 },
        plugins: {
            legend: {
                labels: { color: '#94a3b8', font: { family: 'JetBrains Mono', size: 11 } }
            }
        },
        scales: {
            x: {
                grid: { color: 'rgba(45, 62, 90, 0.25)' },
                ticks: { color: '#64748b', font: { family: 'JetBrains Mono', size: 10 } }
            }
        }
    };

    // 1. Environmental Chart (Temp & Humidity)
    const ctxEnv = document.getElementById('envChart').getContext('2d');
    const envChart = new Chart(ctxEnv, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Temperature (°C)',
                    data: [],
                    borderColor: '#f87171',
                    backgroundColor: 'rgba(248, 113, 113, 0.1)',
                    yAxisID: 'yTemp',
                    tension: 0.35,
                    borderWidth: 2,
                    pointRadius: 2
                },
                {
                    label: 'Humidity (%)',
                    data: [],
                    borderColor: '#38bdf8',
                    backgroundColor: 'rgba(56, 189, 248, 0.1)',
                    yAxisID: 'yHum',
                    tension: 0.35,
                    borderWidth: 2,
                    pointRadius: 2
                }
            ]
        },
        options: {
            ...chartDefaults,
            scales: {
                ...chartDefaults.scales,
                yTemp: {
                    type: 'linear',
                    position: 'left',
                    grid: { color: 'rgba(45, 62, 90, 0.25)' },
                    ticks: { color: '#f87171', font: { family: 'JetBrains Mono', size: 10 } },
                    suggestedMin: 15,
                    suggestedMax: 45
                },
                yHum: {
                    type: 'linear',
                    position: 'right',
                    grid: { drawOnChartArea: false },
                    ticks: { color: '#38bdf8', font: { family: 'JetBrains Mono', size: 10 } },
                    suggestedMin: 20,
                    suggestedMax: 90
                }
            }
        }
    });

    // 2. Motion & Gas Dynamics Chart (Dual Axis)
    const ctxMotion = document.getElementById('motionChart').getContext('2d');
    const motionChart = new Chart(ctxMotion, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Total Acceleration (g)',
                    data: [],
                    borderColor: '#00f2fe',
                    backgroundColor: 'rgba(0, 242, 254, 0.12)',
                    yAxisID: 'yMotion',
                    fill: true,
                    tension: 0.3,
                    borderWidth: 2,
                    pointRadius: 2
                },
                {
                    label: 'Gas Sensor (ADC)',
                    data: [],
                    borderColor: '#fbbf24',
                    backgroundColor: 'transparent',
                    yAxisID: 'yGas',
                    tension: 0.25,
                    borderWidth: 1.5,
                    borderDash: [4, 4],
                    pointRadius: 2
                }
            ]
        },
        options: {
            ...chartDefaults,
            scales: {
                ...chartDefaults.scales,
                yMotion: {
                    type: 'linear',
                    position: 'left',
                    grid: { color: 'rgba(45, 62, 90, 0.25)' },
                    ticks: { color: '#00f2fe', font: { family: 'JetBrains Mono', size: 10 } },
                    suggestedMin: 0.5,
                    suggestedMax: 1.5
                },
                yGas: {
                    type: 'linear',
                    position: 'right',
                    grid: { drawOnChartArea: false },
                    ticks: { color: '#fbbf24', font: { family: 'JetBrains Mono', size: 10 } },
                    suggestedMin: 0,
                    suggestedMax: 500
                }
            }
        }
    });

    function pushChartData(chart, label, datasetValues) {
        chart.data.labels.push(label);
        datasetValues.forEach((val, idx) => {
            chart.data.datasets[idx].data.push(val);
        });

        if (chart.data.labels.length > MAX_CHART_POINTS) {
            chart.data.labels.shift();
            chart.data.datasets.forEach(ds => ds.data.shift());
        }
        chart.update('none');
    }

    // --- Terminal Logging ---
    function updateTerminal(rawLogs) {
        if (!rawLogs || rawLogs.length === 0) return;
        terminalStream.innerHTML = '';
        rawLogs.forEach(entry => {
            const line = document.createElement('div');
            line.className = 'term-row';
            if (entry.includes('error') || entry.includes('Disconnected')) line.classList.add('err');
            else if (entry.includes('warn') || entry.includes('Malformed')) line.classList.add('warn');
            else if (entry.includes('Connected')) line.classList.add('sys');
            line.textContent = entry;
            terminalStream.appendChild(line);
        });
        totalLogsCount = rawLogs.length;
        logCounter.textContent = `${totalLogsCount} logs`;
        terminalStream.scrollTop = terminalStream.scrollHeight;
    }

    // --- Poll Telemetry Endpoint ---
    async function fetchTelemetry() {
        try {
            const res = await fetch('/api/telemetry');
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();

            // Update Connection Status Pill
            if (data.connected) {
                usbStatusBadge.className = 'status-pill online';
                usbStatusText.textContent = 'ONLINE';
                activePortText.textContent = `${data.port || 'COM5'} @ ${data.baudrate || 115200}`;
                lastPacketTime = Date.now();
            } else {
                usbStatusBadge.className = 'status-pill offline';
                usbStatusText.textContent = data.status || 'DISCONNECTED';
            }

            // Uptime
            uptimeDisplay.textContent = formatUptime(data.uptime_ms);

            const now = new Date();
            const timeLabel = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;

            // --- 1. Environmental Sensors (DHT22) ---
            if (data.dht_status === 'ok' && data.temperature !== null) {
                dhtTempBadge.className = 'sensor-badge';
                dhtTempBadge.textContent = 'DHT22 OK';
                dhtHumBadge.className = 'sensor-badge';
                dhtHumBadge.textContent = 'DHT22 OK';

                const temp = parseFloat(data.temperature);
                const hum = parseFloat(data.humidity);

                tempVal.textContent = temp.toFixed(1);
                humVal.textContent = hum.toFixed(1);

                const tempPct = Math.min(100, Math.max(0, ((temp + 10) / 70) * 100));
                tempGauge.style.width = `${tempPct}%`;
                humGauge.style.width = `${Math.min(100, Math.max(0, hum))}%`;

                if (temp > 38.0) {
                    tempStatus.textContent = 'High Heat Alert';
                    tempStatus.style.color = '#ef4444';
                } else if (temp < 10.0) {
                    tempStatus.textContent = 'Cold Warning';
                    tempStatus.style.color = '#38bdf8';
                } else {
                    tempStatus.textContent = 'Nominal Comfort';
                    tempStatus.style.color = '#10b981';
                }

                pushChartData(envChart, timeLabel, [temp, hum]);
            } else {
                dhtTempBadge.className = 'sensor-badge err';
                dhtTempBadge.textContent = data.dht_status ? `DHT ${data.dht_status.toUpperCase()}` : 'OFFLINE';
                dhtHumBadge.className = 'sensor-badge err';
                dhtHumBadge.textContent = data.dht_status ? `DHT ${data.dht_status.toUpperCase()}` : 'OFFLINE';
                tempVal.textContent = '--';
                humVal.textContent = '--';
                tempGauge.style.width = '0%';
                humGauge.style.width = '0%';
            }

            // --- 2. Inertial & Motion Dynamics (MPU6050) ---
            let currentMagG = 1.0;
            if (data.mpu_status === 'ok' && data.accel) {
                mpuStatusBadge.className = 'sensor-badge';
                mpuStatusBadge.textContent = 'MPU6050 OK';

                const ax = parseFloat(data.accel.x);
                const ay = parseFloat(data.accel.y);
                const az = parseFloat(data.accel.z);

                axVal.textContent = ax.toFixed(2);
                ayVal.textContent = ay.toFixed(2);
                azVal.textContent = az.toFixed(2);

                if (data.gyro) {
                    gxVal.textContent = parseFloat(data.gyro.x).toFixed(3);
                    gyVal.textContent = parseFloat(data.gyro.y).toFixed(3);
                    gzVal.textContent = parseFloat(data.gyro.z).toFixed(3);
                }

                const magMps2 = Math.sqrt((ax * ax) + (ay * ay) + (az * az));
                currentMagG = magMps2 / STANDARD_GRAVITY;

                magVal.textContent = currentMagG.toFixed(2);
                const magPct = Math.min(100, Math.max(0, (currentMagG / 2.0) * 100));
                magGauge.style.width = `${magPct}%`;

                const motionClass = classifyMotion(currentMagG);
                motionSubText.textContent = `Dynamic Δ: ${motionClass.delta} g`;
                stateChip.textContent = motionClass.state;

                if (data.emergency_button !== 'PRESSED') {
                    if (motionClass.level === 'alert') {
                        stateChip.className = 'state-chip alert';
                        motionStatusBadge.className = 'motion-summary-badge motion-alert';
                        motionLabel.textContent = 'WORKER STATE: SUDDEN IMPACT / HIGH MOTION';
                    } else if (motionClass.level === 'active') {
                        stateChip.className = 'state-chip active';
                        motionStatusBadge.className = 'motion-summary-badge motion-active';
                        motionLabel.textContent = 'WORKER STATE: ACTIVE MOTION (WALKING)';
                    } else {
                        stateChip.className = 'state-chip';
                        motionStatusBadge.className = 'motion-summary-badge';
                        motionLabel.textContent = 'WORKER STATE: STATIONARY / RESTING';
                    }
                }
            }

            // --- 3. MQ-135 Air Quality Sensor ---
            const gasRaw = data.gas_adc !== undefined && data.gas_adc !== null ? data.gas_adc : null;
            const gasVolt = data.gas_voltage_est !== undefined && data.gas_voltage_est !== null ? data.gas_voltage_est : null;

            if (gasRaw !== null) {
                gasVal.textContent = gasRaw;
                gasVoltageText.textContent = `Voltage: ${gasVolt !== null ? gasVolt.toFixed(3) : '--'} V`;

                // Gauge (0 to 1000 ADC range focus)
                const gasPct = Math.min(100, Math.max(0, (gasRaw / 1000) * 100));
                gasGauge.style.width = `${gasPct}%`;

                if (gasRaw > 600) {
                    gasStatusChip.className = 'state-chip alert';
                    gasStatusChip.textContent = 'ELEVATED GAS';
                } else if (gasRaw > 250) {
                    gasStatusChip.className = 'state-chip active';
                    gasStatusChip.textContent = 'MODERATE';
                } else {
                    gasStatusChip.className = 'state-chip';
                    gasStatusChip.textContent = 'NOMINAL (CLEAN)';
                }

                pushChartData(motionChart, timeLabel, [currentMagG, gasRaw]);
            } else {
                gasVal.textContent = '--';
                gasVoltageText.textContent = 'Voltage: -- V';
                gasGauge.style.width = '0%';
                pushChartData(motionChart, timeLabel, [currentMagG, 0]);
            }

            // --- 4. Emergency SOS Push Button ---
            const isEmergency = data.emergency_button === 'PRESSED';
            if (isEmergency) {
                emergencyBanner.style.display = 'block';
                cardEmergency.classList.add('active-emergency');
                sosIcon.textContent = '🚨';
                sosStatusText.textContent = 'PRESSED!';
                sosChip.className = 'state-chip emergency';
                sosChip.textContent = 'EMERGENCY TRIGGERED';

                motionStatusBadge.className = 'motion-summary-badge motion-alert';
                motionLabel.textContent = '🚨 WORKER EMERGENCY ACTIVATED (GPIO 27) 🚨';
            } else {
                emergencyBanner.style.display = 'none';
                cardEmergency.classList.remove('active-emergency');
                sosIcon.textContent = '🔘';
                sosStatusText.textContent = 'STANDBY';
                sosChip.className = 'state-chip';
                sosChip.textContent = 'NORMAL';
            }

            // Raw stream logs
            if (data.raw_log) {
                updateTerminal(data.raw_log);
            }

        } catch (err) {
            usbStatusBadge.className = 'status-pill offline';
            usbStatusText.textContent = 'SERVER DISCONNECTED';
        }
    }

    // Ping Counter
    setInterval(() => {
        if (!lastPacketTime) {
            lastSeenDisplay.textContent = 'Never';
            return;
        }
        const diffSec = Math.floor((Date.now() - lastPacketTime) / 1000);
        lastSeenDisplay.textContent = diffSec < 2 ? 'Just now' : `${diffSec}s ago`;
    }, 1000);

    // Ports Loader
    async function loadDetectedPorts() {
        try {
            const res = await fetch('/api/ports');
            const data = await res.json();
            portSelect.innerHTML = '';
            if (data.ports && data.ports.length > 0) {
                data.ports.forEach(p => {
                    const opt = document.createElement('option');
                    opt.value = p.device;
                    opt.textContent = `${p.device} (${p.description || 'Serial'})`;
                    if (p.device === 'COM5') opt.selected = true;
                    portSelect.appendChild(opt);
                });
            } else {
                const opt = document.createElement('option');
                opt.value = '';
                opt.textContent = 'No Serial Ports Detected';
                portSelect.appendChild(opt);
            }
        } catch (err) {
            console.error('Failed to query ports:', err);
        }
    }

    btnRefreshPorts.addEventListener('click', loadDetectedPorts);

    btnApplyConfig.addEventListener('click', async () => {
        const port = portSelect.value;
        const baudrate = parseInt(baudSelect.value, 10);
        if (!port) return;

        btnApplyConfig.disabled = true;
        btnApplyConfig.textContent = 'Switching...';

        try {
            const res = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ port, baudrate })
            });
            const result = await res.json();
            if (result.success) {
                activePortText.textContent = `${port} @ ${baudrate}`;
            }
        } catch (err) {
            console.error('Error switching port:', err);
        } finally {
            btnApplyConfig.disabled = false;
            btnApplyConfig.textContent = 'Switch Port';
        }
    });

    btnClearLogs.addEventListener('click', () => {
        terminalStream.innerHTML = '<div class="term-row sys">[SENTINEL-X] Terminal buffer cleared.</div>';
    });

    // Start
    loadDetectedPorts();
    fetchTelemetry();
    pollInterval = setInterval(fetchTelemetry, 500);
});

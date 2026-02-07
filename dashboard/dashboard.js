// Polymarket Trading Dashboard Logic

let supabase;
let pnlChart;

// Initialize dashboard
async function initDashboard() {
    // Check if config exists
    if (!window.SUPABASE_CONFIG || window.SUPABASE_CONFIG.url === 'YOUR_SUPABASE_URL') {
        document.getElementById('setup-warning').style.display = 'block';
        document.getElementById('loading').style.display = 'none';
        return;
    }

    // Initialize Supabase client
    try {
        supabase = window.supabase.createClient(
            window.SUPABASE_CONFIG.url,
            window.SUPABASE_CONFIG.anonKey
        );

        // Load dashboard data
        await loadDashboard();

        // Auto-refresh every 30 seconds
        setInterval(loadDashboard, 30000);
    } catch (error) {
        showError('Failed to connect to Supabase: ' + error.message);
    }
}

// Load all dashboard data
async function loadDashboard() {
    try {
        document.getElementById('loading').style.display = 'block';
        document.getElementById('error-message').style.display = 'none';

        // Load trades
        const trades = await loadTrades();

        // Calculate metrics
        const metrics = calculateMetrics(trades);

        // Update UI
        updateMetrics(metrics);
        updateTradesTable(trades);
        updatePnLChart(trades);

        // Show dashboard
        document.getElementById('loading').style.display = 'none';
        document.getElementById('dashboard').style.display = 'block';

        // Update timestamp
        document.getElementById('last-updated').textContent = new Date().toLocaleString();

    } catch (error) {
        showError('Failed to load dashboard: ' + error.message);
    }
}

// Load trades from Supabase
async function loadTrades() {
    const { data, error } = await supabase
        .from('polymarket_audit_log')
        .select('*')
        .eq('event_type', 'TRADE')
        .order('created_at', { ascending: false })
        .limit(100);

    if (error) throw error;
    return data || [];
}

// Calculate metrics from trades
function calculateMetrics(trades) {
    let totalPnL = 0;
    let winningTrades = 0;
    let losingTrades = 0;
    let openPositions = new Set();

    trades.forEach(trade => {
        const reward = trade.reward_earned || 0;
        totalPnL += reward;

        if (reward > 0) winningTrades++;
        else if (reward < 0) losingTrades++;

        if (trade.action === 'BUY' || trade.action === 'FARM_ADJUSTED') {
            openPositions.add(trade.market_id);
        }
    });

    const totalTrades = winningTrades + losingTrades;
    const winRate = totalTrades > 0 ? (winningTrades / totalTrades) * 100 : 0;

    return {
        totalPnL,
        winRate,
        openPositions: openPositions.size,
        winningTrades,
        losingTrades,
        totalTrades
    };
}

// Update metrics cards
function updateMetrics(metrics) {
    // Total P&L
    const pnlEl = document.getElementById('total-pnl');
    pnlEl.textContent = formatCurrency(metrics.totalPnL);
    pnlEl.className = 'metric-value ' + (metrics.totalPnL >= 0 ? 'positive' : 'negative');

    document.getElementById('pnl-change').textContent =
        `${metrics.winningTrades}W / ${metrics.losingTrades}L`;

    // Win Rate
    const winRateEl = document.getElementById('win-rate');
    winRateEl.textContent = metrics.winRate.toFixed(1) + '%';
    winRateEl.className = 'metric-value ' + (metrics.winRate >= 60 ? 'positive' : '');

    document.getElementById('win-rate-change').textContent =
        `${metrics.totalTrades} total trades`;

    // Open Positions
    document.getElementById('open-positions').textContent = metrics.openPositions;
}

// Update trades table
function updateTradesTable(trades) {
    const tbody = document.getElementById('trades-body');

    if (trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:#888;">No trades yet</td></tr>';
        return;
    }

    tbody.innerHTML = trades.slice(0, 20).map(trade => `
        <tr>
            <td>${formatDate(trade.created_at)}</td>
            <td>${formatMarketId(trade.market_id)}</td>
            <td>${trade.action}</td>
            <td>${formatCurrency(trade.trade_size || 0)}</td>
            <td>${formatPercent(trade.alpha)}</td>
            <td class="${(trade.reward_earned || 0) >= 0 ? 'positive' : 'negative'}">
                ${formatCurrency(trade.reward_earned || 0)}
            </td>
            <td><span class="status-badge success">SUCCESS</span></td>
        </tr>
    `).join('');
}

// Update P&L chart
function updatePnLChart(trades) {
    const ctx = document.getElementById('pnl-chart');

    // Calculate cumulative P&L over time
    let cumulative = 0;
    const chartData = trades.reverse().map(trade => {
        cumulative += (trade.reward_earned || 0);
        return {
            x: new Date(trade.created_at),
            y: cumulative
        };
    });

    if (pnlChart) {
        pnlChart.destroy();
    }

    pnlChart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [{
                label: 'Cumulative P&L',
                data: chartData,
                borderColor: '#667eea',
                backgroundColor: 'rgba(102, 126, 234, 0.1)',
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: 'hour'
                    }
                },
                y: {
                    ticks: {
                        callback: function(value) {
                            return '$' + value.toFixed(2);
                        }
                    }
                }
            },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return 'P&L: $' + context.parsed.y.toFixed(2);
                        }
                    }
                }
            }
        }
    });
}

// Utility functions
function formatCurrency(value) {
    return '$' + value.toFixed(2);
}

function formatPercent(value) {
    return (value * 100).toFixed(1) + '%';
}

function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
}

function formatMarketId(id) {
    return id.substring(0, 8) + '...';
}

function showError(message) {
    document.getElementById('error-message').textContent = message;
    document.getElementById('error-message').style.display = 'block';
    document.getElementById('loading').style.display = 'none';
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', initDashboard);

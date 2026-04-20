/** @odoo-module **/
import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

class AgxDashboard extends Component {
    static template = "agri_export_management.AgxDashboard";

    setup() {
        this.orm          = useService("orm");
        this.action       = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            loading: true,
            data: null,
            season_id: false,
            seasons: [],
            date_from: this._firstDayOfMonth(),
            date_to: this._today(),
            charts: {},
        });

        onMounted(() => this._loadData());
        onWillUnmount(() => this._destroyCharts());
    }

    // ── Helpers exposed to template ───────────────────────────────────
    fmt(val, decimals = 0) {
        if (val === undefined || val === null) return "0";
        return Number(val).toLocaleString("en-US", {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        });
    }

    fmtPct(val) { return `${this.fmt(val, 1)}%`; }

    _today() {
        return new Date().toISOString().split("T")[0];
    }

    _firstDayOfMonth() {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
    }

    // ── Data loading ──────────────────────────────────────────────────
    async _loadData() {
        this.state.loading = true;
        try {
            const seasons = await this.orm.searchRead(
                "agx.season",
                [["state", "!=", "cancelled"]],
                ["id", "name", "state"],
                { order: "date_start desc", limit: 20 }
            );
            this.state.seasons = seasons;

            const data = await this.orm.call(
                "agx.dashboard",
                "get_dashboard_data",
                [],
                {
                    season_id: this.state.season_id || false,
                    date_from: this.state.date_from,
                    date_to:   this.state.date_to,
                }
            );
            this.state.data = data;
            this.state.loading = false;
            setTimeout(() => this._renderCharts(), 100);
        } catch (e) {
            console.error("AGX Dashboard error:", e);
            this.state.loading = false;
            this.state.data = this._emptyData();
            setTimeout(() => this._renderCharts(), 100);
        }
    }

    _emptyData() {
        return {
            kpis: {
                revenue: 0, logistics_cost: 0, gross_profit: 0,
                margin_pct: 0, shipped_count: 0, pending_count: 0,
                active_batches: 0, open_evaluations: 0,
            },
            shipments_by_destination: [],
            revenue_by_season: [],
            top_customers: [],
            monthly_shipments: [],
            yield_by_season: [],
        };
    }

    // ── Charts ────────────────────────────────────────────────────────
    _destroyCharts() {
        Object.values(this.state.charts).forEach(c => { try { c.destroy(); } catch(e){} });
        this.state.charts = {};
    }

    _isDark() {
        // Detect Odoo dark mode
        return document.documentElement.getAttribute("data-color-scheme") === "dark"
            || document.body.classList.contains("o_dark");
    }

    _chartColors() {
        const dark = this._isDark();
        return {
            text:    dark ? "#CBD5E1" : "#374151",
            grid:    dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
            tooltip: dark ? "#1C2E42"  : "#FFFFFF",
            tooltipBorder: dark ? "#22364F" : "#E5E7EB",
        };
    }

    _renderCharts() {
        const d = this.state.data;
        if (!d || !window.Chart) return;
        this._destroyCharts();

        const cc = this._chartColors();
        const base = {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: cc.text, font: { family: "inherit" } } },
                tooltip: {
                    backgroundColor: cc.tooltip,
                    titleColor: cc.text,
                    bodyColor: cc.text,
                    borderColor: cc.tooltipBorder,
                    borderWidth: 1,
                },
            },
        };
        const scales = {
            x: { ticks: { color: cc.text }, grid: { color: cc.grid } },
            y: { ticks: { color: cc.text }, grid: { color: cc.grid } },
        };

        // 1. Revenue vs Cost vs Profit
        const barEl = document.getElementById("agx-chart-revenue");
        if (barEl && d.revenue_by_season.length) {
            this.state.charts.bar = new window.Chart(barEl, {
                type: "bar",
                data: {
                    labels: d.revenue_by_season.map(s => s.name),
                    datasets: [
                        { label: "Revenue",        data: d.revenue_by_season.map(s => s.revenue), backgroundColor: "rgba(34,197,94,0.8)",  borderRadius: 4 },
                        { label: "Logistics Cost", data: d.revenue_by_season.map(s => s.cost),    backgroundColor: "rgba(248,113,113,0.8)", borderRadius: 4 },
                        { label: "Gross Profit",   data: d.revenue_by_season.map(s => s.profit),  backgroundColor: "rgba(96,165,250,0.8)",  borderRadius: 4 },
                    ],
                },
                options: { ...base, scales },
            });
        }

        // 2. Shipments by destination
        const pieEl = document.getElementById("agx-chart-destinations");
        if (pieEl && d.shipments_by_destination.length) {
            this.state.charts.pie = new window.Chart(pieEl, {
                type: "doughnut",
                data: {
                    labels: d.shipments_by_destination.map(x => x.name),
                    datasets: [{
                        data: d.shipments_by_destination.map(x => x.count),
                        backgroundColor: ["#22C55E","#60A5FA","#FBBF24","#A78BFA","#14B8A6","#F87171","#FB923C","#E879F9"],
                        borderWidth: 2,
                        borderColor: "transparent",
                    }],
                },
                options: { ...base, cutout: "65%" },
            });
        }

        // 3. Monthly shipments + revenue
        const lineEl = document.getElementById("agx-chart-monthly");
        if (lineEl && d.monthly_shipments.length) {
            this.state.charts.line = new window.Chart(lineEl, {
                type: "line",
                data: {
                    labels: d.monthly_shipments.map(x => x.month),
                    datasets: [
                        { label: "Shipments", data: d.monthly_shipments.map(x => x.count), borderColor: "#22C55E", backgroundColor: "rgba(34,197,94,0.1)", tension: 0.4, fill: true, pointBackgroundColor: "#22C55E" },
                        { label: "Revenue (k)", data: d.monthly_shipments.map(x => x.revenue / 1000), borderColor: "#60A5FA", backgroundColor: "rgba(96,165,250,0.1)", tension: 0.4, fill: true, pointBackgroundColor: "#60A5FA", yAxisID: "y2" },
                    ],
                },
                options: {
                    ...base,
                    scales: {
                        ...scales,
                        y2: { position: "right", ticks: { color: cc.text }, grid: { drawOnChartArea: false } },
                    },
                },
            });
        }

        // 4. Yield % by season
        const yieldEl = document.getElementById("agx-chart-yield");
        if (yieldEl && d.yield_by_season.length) {
            this.state.charts.yield = new window.Chart(yieldEl, {
                type: "bar",
                data: {
                    labels: d.yield_by_season.map(x => x.name),
                    datasets: [{
                        label: "Yield %",
                        data: d.yield_by_season.map(x => x.yield_pct),
                        backgroundColor: d.yield_by_season.map(x =>
                            x.yield_pct >= 80 ? "rgba(34,197,94,0.8)"
                            : x.yield_pct >= 60 ? "rgba(251,191,36,0.8)"
                            : "rgba(248,113,113,0.8)"
                        ),
                        borderRadius: 4,
                    }],
                },
                options: {
                    ...base,
                    indexAxis: "y",
                    scales: {
                        x: { ...scales.x, max: 100, ticks: { ...scales.x.ticks, callback: v => v + "%" } },
                        y: scales.y,
                    },
                },
            });
        }
    }

    // ── Navigation ────────────────────────────────────────────────────
    async _navigate(model, domain, view = "list") {
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            views: [[false, view], [false, "form"]],
            domain: domain || [],
            target: "current",
        });
    }

    onEvaluationsClick() { this._navigate("agx.evaluation", []); }
    onBatchesClick()     { this._navigate("agx.batch", [["state","in",["draft","in_progress"]]]); }
    onShipmentsClick()   { this._navigate("agx.shipment", [["state","in",["draft","reserved"]]]); }
    onShippedClick()     { this._navigate("agx.shipment", [["state","=","shipped"]]); }

    async onSeasonChange(ev) {
        this.state.season_id = parseInt(ev.target.value) || false;
        await this._loadData();
    }
    async onDateChange() { await this._loadData(); }
    async onRefresh() {
        await this._loadData();
        this.notification.add("Dashboard refreshed", { type: "success" });
    }
}

registry.category("actions").add("agx_dashboard_action", AgxDashboard);
export { AgxDashboard };

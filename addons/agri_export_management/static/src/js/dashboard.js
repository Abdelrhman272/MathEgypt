/** @odoo-module **/
/**
 * AGX Agricultural Export Dashboard — OWL Component
 * A rich client-action dashboard with live data from the server.
 *
 * Architecture:
 *   - OWL Component registered as a client action
 *   - Fetches data via rpc calls to agx.dashboard model
 *   - Chart.js for bar, pie, and line charts
 *   - Renders fully inside Odoo's web client
 */

import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

class AgxDashboard extends Component {
    static template = "agri_export_management.AgxDashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
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

        onMounted(() => {
            this._enablePageScroll();
            this._loadData();
        });
        onWillUnmount(() => {
            this._restorePageScroll();
            this._destroyCharts();
        });
    }

    // ── Helpers ─────────────────────────────────────────────────────

    _enablePageScroll() {
        const root = this.el;
        const action = root?.closest?.(".o_action") || root?.parentElement;
        const content = root?.closest?.(".o_content") || action?.querySelector?.(".o_content");
        this._agxScrollTargets = [content, action].filter(Boolean);
        for (const el of this._agxScrollTargets) {
            if (!el) continue;
            el.dataset.agxPrevOverflowY = el.style.overflowY || "";
            el.dataset.agxPrevOverflow = el.style.overflow || "";
            el.style.overflowY = "auto";
            el.style.overflowX = "hidden";
        }
    }

    _restorePageScroll() {
        for (const el of this._agxScrollTargets || []) {
            if (!el) continue;
            el.style.overflowY = el.dataset.agxPrevOverflowY || "";
            el.style.overflow = el.dataset.agxPrevOverflow || "";
            delete el.dataset.agxPrevOverflowY;
            delete el.dataset.agxPrevOverflow;
        }
        this._agxScrollTargets = [];
    }
    _today() {
        return new Date().toISOString().split("T")[0];
    }

    _firstDayOfMonth() {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
    }

    _fmt(val, decimals = 0) {
        if (val === undefined || val === null) return "0";
        return Number(val).toLocaleString("en-US", {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        });
    }

    _fmtPct(val) {
        return `${this._fmt(val, 1)}%`;
    }

    // ── Data loading ─────────────────────────────────────────────────
    async _loadData() {
        this.state.loading = true;
        try {
            // Load seasons list
            const seasons = await this.orm.searchRead(
                "agx.season",
                [["state", "!=", "cancelled"]],
                ["id", "name", "crop_id", "state"],
                { order: "date_start desc", limit: 20 }
            );
            this.state.seasons = seasons;

            // Load dashboard data through the ORM service.
            // In Odoo 19, model methods should go through orm.call(...)
            // instead of manually using /web/dataset/call_kw.
            const data = await this.orm.call(
                "agx.dashboard",
                "get_dashboard_data",
                [],
                {
                    season_id: this.state.season_id || false,
                    date_from: this.state.date_from,
                    date_to: this.state.date_to,
                }
            );
            this.state.data = data;
            this.state.loading = false;

            // Render charts after DOM update
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

    // ── Chart rendering ───────────────────────────────────────────────
    _destroyCharts() {
        Object.values(this.state.charts).forEach(c => { try { c.destroy(); } catch(e){} });
        this.state.charts = {};
    }

    _getChartDefaults() {
        return {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: "#CBD5E1", font: { family: "DM Sans" } } },
                tooltip: {
                    backgroundColor: "#1C2E42",
                    titleColor: "#F8FAFC",
                    bodyColor: "#CBD5E1",
                    borderColor: "#22364F",
                    borderWidth: 1,
                },
            },
        };
    }

    _renderCharts() {
        const d = this.state.data;
        if (!d) return;
        this._destroyCharts();

        // Chart.js must be loaded from the page
        const Chart = window.Chart;
        if (!Chart) return;

        const defaults = this._getChartDefaults();

        // ── 1. Revenue vs Cost bar chart ──────────────────────────────
        const barEl = document.getElementById("agx-chart-revenue");
        if (barEl) {
            const labels = d.revenue_by_season.map(s => s.name);
            const revenues = d.revenue_by_season.map(s => s.revenue);
            const costs = d.revenue_by_season.map(s => s.cost);
            const profits = d.revenue_by_season.map(s => s.profit);
            this.state.charts.bar = new Chart(barEl, {
                type: "bar",
                data: {
                    labels,
                    datasets: [
                        {
                            label: "Revenue",
                            data: revenues,
                            backgroundColor: "rgba(34,197,94,0.8)",
                            borderRadius: 4,
                        },
                        {
                            label: "Logistics Cost",
                            data: costs,
                            backgroundColor: "rgba(248,113,113,0.8)",
                            borderRadius: 4,
                        },
                        {
                            label: "Gross Profit",
                            data: profits,
                            backgroundColor: "rgba(96,165,250,0.8)",
                            borderRadius: 4,
                        },
                    ],
                },
                options: {
                    ...defaults,
                    scales: {
                        x: { ticks: { color: "#64748B" }, grid: { color: "#22364F" } },
                        y: { ticks: { color: "#64748B" }, grid: { color: "#22364F" } },
                    },
                },
            });
        }

        // ── 2. Shipments by destination pie ───────────────────────────
        const pieEl = document.getElementById("agx-chart-destinations");
        if (pieEl && d.shipments_by_destination.length) {
            const COLORS = [
                "#22C55E","#60A5FA","#FBBF24","#A78BFA",
                "#14B8A6","#F87171","#FB923C","#E879F9",
            ];
            this.state.charts.pie = new Chart(pieEl, {
                type: "doughnut",
                data: {
                    labels: d.shipments_by_destination.map(x => x.name),
                    datasets: [{
                        data: d.shipments_by_destination.map(x => x.count),
                        backgroundColor: COLORS,
                        borderWidth: 2,
                        borderColor: "#0D1B2A",
                    }],
                },
                options: {
                    ...defaults,
                    cutout: "65%",
                },
            });
        }

        // ── 3. Monthly shipments line chart ───────────────────────────
        const lineEl = document.getElementById("agx-chart-monthly");
        if (lineEl && d.monthly_shipments.length) {
            this.state.charts.line = new Chart(lineEl, {
                type: "line",
                data: {
                    labels: d.monthly_shipments.map(x => x.month),
                    datasets: [
                        {
                            label: "Shipments",
                            data: d.monthly_shipments.map(x => x.count),
                            borderColor: "#22C55E",
                            backgroundColor: "rgba(34,197,94,0.1)",
                            tension: 0.4,
                            fill: true,
                            pointBackgroundColor: "#22C55E",
                        },
                        {
                            label: "Revenue (k)",
                            data: d.monthly_shipments.map(x => x.revenue / 1000),
                            borderColor: "#60A5FA",
                            backgroundColor: "rgba(96,165,250,0.1)",
                            tension: 0.4,
                            fill: true,
                            pointBackgroundColor: "#60A5FA",
                            yAxisID: "y2",
                        },
                    ],
                },
                options: {
                    ...defaults,
                    scales: {
                        x: { ticks: { color: "#64748B" }, grid: { color: "#22364F" } },
                        y: {
                            ticks: { color: "#64748B" },
                            grid: { color: "#22364F" },
                            title: { display: true, text: "Shipments", color: "#64748B" },
                        },
                        y2: {
                            position: "right",
                            ticks: { color: "#64748B" },
                            grid: { drawOnChartArea: false },
                            title: { display: true, text: "Revenue (k)", color: "#64748B" },
                        },
                    },
                },
            });
        }

        // ── 4. Yield % by season horizontal bar ───────────────────────
        const yieldEl = document.getElementById("agx-chart-yield");
        if (yieldEl && d.yield_by_season.length) {
            this.state.charts.yield = new Chart(yieldEl, {
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
                    ...defaults,
                    indexAxis: "y",
                    scales: {
                        x: {
                            ticks: { color: "#64748B", callback: v => v + "%" },
                            grid: { color: "#22364F" },
                            max: 100,
                        },
                        y: { ticks: { color: "#64748B" }, grid: { color: "#22364F" } },
                    },
                },
            });
        }
    }

    // ── Navigation ───────────────────────────────────────────────────
    async _navigate(model, domain, view = "list") {
        const views = view === "form"
            ? [[false, "form"]]
            : [[false, view], [false, "form"]];

        await this.action.doAction({
            type: "ir.actions.act_window",
            name: "Open Records",
            res_model: model,
            views,
            view_mode: view === "form" ? "form" : `${view},form`,
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

    async onDateChange() {
        await this._loadData();
    }

    async onRefresh() {
        await this._loadData();
        this.notification.add("Dashboard refreshed", { type: "success" });
    }
}

registry.category("actions").add("agx_dashboard_action", AgxDashboard);

export { AgxDashboard };

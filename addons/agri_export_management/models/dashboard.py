# -*- coding: utf-8 -*-
"""
dashboard.py — Agricultural Export Dashboard
=============================================
A singleton-per-company model that aggregates KPIs for the main
dashboard screen.  All metrics are computed on demand from the
date_from / date_to filter the user sets on the form.

Design decisions
----------------
* One record per company (enforced by a DB unique constraint).
* All fields are non-stored computes — no caching, always fresh.
* ``action_open_dashboard`` creates the record if it doesn't exist
  and opens the form view directly.
* Season filter: when ``season_id`` is set, metrics are scoped to
  that season regardless of date_from / date_to.

KPI groups
----------
Operations : open evaluations, pending receipts, active batches,
             pending shipments, shipped shipments.
Reservations: reserved lots qty.
Profitability: logistics cost, revenue, gross profit, margin % — all
              from agx.shipment records in the selected period/season.
Highlights  : top destination, top customer, highest cost shipment,
              highest margin shipment.
"""

from collections import Counter

from odoo import _, api, fields, models


class AgxDashboard(models.Model):
    """Agricultural Export Dashboard — one record per company."""

    _name = "agx.dashboard"
    _description = "Agricultural Export Dashboard"
    _order = "company_id"

    _agx_dashboard_company_unique = models.Constraint(
        "UNIQUE(company_id)",
        "Only one dashboard record is allowed per company.",
    )

    # ------------------------------------------------------------------
    # Identity / filters
    # ------------------------------------------------------------------
    name = fields.Char(compute="_compute_name")
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        readonly=True,
    )
    date_from = fields.Date(
        default=lambda self: fields.Date.context_today(self).replace(day=1),
        required=True,
        help="Start of the reporting period.",
    )
    date_to = fields.Date(
        default=fields.Date.context_today,
        required=True,
        help="End of the reporting period.",
    )
    season_id = fields.Many2one(
        "agx.season",
        string="Filter by Season",
        help=(
            "When set, all metrics are scoped to this season. "
            "Date filters are ignored for season-scoped metrics."
        ),
    )

    # ------------------------------------------------------------------
    # Operations KPIs
    # ------------------------------------------------------------------
    open_evaluations_count = fields.Integer(compute="_compute_metrics")
    approved_evaluations_count = fields.Integer(compute="_compute_metrics")
    pending_purchases_count = fields.Integer(compute="_compute_metrics")
    pending_incoming_receipts_count = fields.Integer(compute="_compute_metrics")
    active_batches_count = fields.Integer(compute="_compute_metrics")
    pending_shipments_count = fields.Integer(compute="_compute_metrics")
    reserved_lots_qty = fields.Float(compute="_compute_metrics")
    shipped_shipments_count = fields.Integer(compute="_compute_metrics")

    # ------------------------------------------------------------------
    # Profitability KPIs
    # ------------------------------------------------------------------
    logistics_cost_period = fields.Monetary(
        currency_field="currency_id", compute="_compute_metrics"
    )
    revenue_period = fields.Monetary(
        currency_field="currency_id", compute="_compute_metrics"
    )
    gross_profit_period = fields.Monetary(
        currency_field="currency_id", compute="_compute_metrics"
    )
    margin_pct_period = fields.Float(
        compute="_compute_metrics", digits=(16, 2)
    )

    # ------------------------------------------------------------------
    # Highlights
    # ------------------------------------------------------------------
    top_destination_name = fields.Char(compute="_compute_metrics")
    top_customer_name = fields.Char(compute="_compute_metrics")
    highest_cost_shipment_name = fields.Char(compute="_compute_metrics")
    highest_margin_shipment_name = fields.Char(compute="_compute_metrics")

    # ------------------------------------------------------------------
    # Season-level metrics (only when season_id is set)
    # ------------------------------------------------------------------
    season_total_input_qty = fields.Float(compute="_compute_season_metrics")
    season_total_output_qty = fields.Float(compute="_compute_season_metrics")
    season_yield_pct = fields.Float(
        compute="_compute_season_metrics", digits=(16, 2)
    )
    season_total_revenue = fields.Monetary(
        currency_field="currency_id", compute="_compute_season_metrics"
    )
    season_gross_profit = fields.Monetary(
        currency_field="currency_id", compute="_compute_season_metrics"
    )
    season_margin_pct = fields.Float(
        compute="_compute_season_metrics", digits=(16, 2)
    )

    # ------------------------------------------------------------------
    # Computed: name
    # ------------------------------------------------------------------
    @api.depends("company_id")
    def _compute_name(self):
        for rec in self:
            rec.name = _("Agricultural Export Dashboard")

    # ------------------------------------------------------------------
    # Domain builders
    # ------------------------------------------------------------------
    def _get_eval_domain(self):
        """Build evaluation domain for current period or season."""
        self.ensure_one()
        base = [("company_id", "=", self.company_id.id)]
        if self.season_id:
            return base + [("season_id", "=", self.season_id.id)]
        return base + [
            ("evaluation_date", ">=", self.date_from),
            ("evaluation_date", "<=", self.date_to),
        ]

    def _get_shipment_domain(self):
        """Build shipment domain for current period or season."""
        self.ensure_one()
        base = [("company_id", "=", self.company_id.id)]
        if self.season_id:
            return base + [("season_id", "=", self.season_id.id)]
        return base + [
            ("shipment_date", ">=", self.date_from),
            ("shipment_date", "<=", self.date_to),
        ]

    def _get_batch_domain(self):
        """Build batch domain for current period or season."""
        self.ensure_one()
        base = [("company_id", "=", self.company_id.id)]
        if self.season_id:
            return base + [("season_id", "=", self.season_id.id)]
        return base + [
            ("batch_date", ">=", self.date_from),
            ("batch_date", "<=", self.date_to),
        ]

    def _get_pending_receipt_domain(self):
        """Build domain for pending incoming receipts."""
        self.ensure_one()
        return [
            ("company_id", "=", self.company_id.id),
            ("picking_type_id.code", "=", "incoming"),
            ("agx_evaluation_id", "!=", False),
            ("scheduled_date", ">=", self.date_from),
            ("scheduled_date", "<=", "{} 23:59:59".format(self.date_to)),
            ("state", "not in", ["done", "cancel"]),
        ]

    # ------------------------------------------------------------------
    # Computed: main metrics
    # ------------------------------------------------------------------
    @api.depends("company_id", "date_from", "date_to", "season_id")
    def _compute_metrics(self):
        """Compute all dashboard KPIs.

        Uses search_count for counts and mapped() for aggregations
        to stay within ORM best practices and avoid raw SQL.
        """
        Evaluation = self.env["agx.evaluation"]
        Shipment = self.env["agx.shipment"]
        Batch = self.env["agx.batch"]
        Picking = self.env["stock.picking"]

        for rec in self:
            eval_domain = rec._get_eval_domain()
            shipment_domain = rec._get_shipment_domain()
            batch_domain = rec._get_batch_domain()
            pending_receipt_domain = rec._get_pending_receipt_domain()

            # Operations
            rec.open_evaluations_count = Evaluation.search_count(
                eval_domain + [("state", "=", "draft")]
            )
            rec.approved_evaluations_count = Evaluation.search_count(
                eval_domain + [("state", "=", "approved")]
            )
            rec.pending_purchases_count = Evaluation.search_count(
                eval_domain + [("state", "=", "approved"), ("po_id", "=", False)]
            )
            rec.pending_incoming_receipts_count = Picking.search_count(
                pending_receipt_domain
            )
            rec.active_batches_count = Batch.search_count(
                batch_domain + [("state", "in", ["draft", "in_progress"])]
            )
            rec.pending_shipments_count = Shipment.search_count(
                shipment_domain + [("state", "in", ["draft", "reserved"])]
            )

            # Shipment aggregates
            shipments = Shipment.search(shipment_domain)
            rec.reserved_lots_qty = sum(
                shipments.mapped("lot_line_ids.reserved_qty")
            )
            rec.shipped_shipments_count = len(
                shipments.filtered(lambda s: s.state == "shipped")
            )
            rec.logistics_cost_period = sum(
                shipments.mapped("total_logistics_cost")
            )
            rec.revenue_period = sum(shipments.mapped("revenue_amount"))
            rec.gross_profit_period = sum(
                shipments.mapped("gross_profit_amount")
            )
            rec.margin_pct_period = (
                (rec.gross_profit_period / rec.revenue_period * 100.0)
                if rec.revenue_period
                else 0.0
            )

            # Highlights — top destination and customer by shipment count
            destinations = Counter(
                shipments.filtered(lambda s: s.destination_id).mapped(
                    "destination_id.name"
                )
            )
            customers = Counter(
                shipments.filtered(lambda s: s.customer_id).mapped(
                    "customer_id.display_name"
                )
            )
            rec.top_destination_name = (
                destinations.most_common(1)[0][0] if destinations else False
            )
            rec.top_customer_name = (
                customers.most_common(1)[0][0] if customers else False
            )
            highest_cost = shipments.sorted(
                lambda s: s.total_logistics_cost, reverse=True
            )[:1]
            highest_margin = shipments.filtered(
                lambda s: s.revenue_amount
            ).sorted(lambda s: s.gross_margin_pct, reverse=True)[:1]
            rec.highest_cost_shipment_name = (
                highest_cost.name if highest_cost else False
            )
            rec.highest_margin_shipment_name = (
                highest_margin.name if highest_margin else False
            )

    # ------------------------------------------------------------------
    # Computed: season-level metrics
    # ------------------------------------------------------------------
    @api.depends("season_id", "company_id")
    def _compute_season_metrics(self):
        """Compute season-wide production and profitability metrics.

        Only active when ``season_id`` is set; otherwise zeros out.
        """
        for rec in self:
            if not rec.season_id:
                rec.season_total_input_qty = 0.0
                rec.season_total_output_qty = 0.0
                rec.season_yield_pct = 0.0
                rec.season_total_revenue = 0.0
                rec.season_gross_profit = 0.0
                rec.season_margin_pct = 0.0
                continue

            batches = self.env["agx.batch"].search(
                [
                    ("season_id", "=", rec.season_id.id),
                    ("company_id", "=", rec.company_id.id),
                    ("state", "=", "done"),
                ]
            )
            total_in = sum(batches.mapped("input_qty"))
            total_out = sum(batches.mapped("output_qty"))
            rec.season_total_input_qty = total_in
            rec.season_total_output_qty = total_out
            rec.season_yield_pct = (
                (total_out / total_in * 100.0) if total_in else 0.0
            )

            shipments = self.env["agx.shipment"].search(
                [
                    ("season_id", "=", rec.season_id.id),
                    ("company_id", "=", rec.company_id.id),
                ]
            )
            rev = sum(shipments.mapped("revenue_amount"))
            cost = sum(shipments.mapped("total_logistics_cost"))
            profit = rev - cost
            rec.season_total_revenue = rev
            rec.season_gross_profit = profit
            rec.season_margin_pct = (
                (profit / rev * 100.0) if rev else 0.0
            )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    @api.model
    def action_open_dashboard(self):
        """Open (or create) the dashboard for the current company."""
        dashboard = self.search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        if not dashboard:
            dashboard = self.create({"company_id": self.env.company.id})
        return {
            "type": "ir.actions.act_window",
            "name": _("Agricultural Export Dashboard"),
            "res_model": "agx.dashboard",
            "res_id": dashboard.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------
    def _action_for_model(self, model_name, title, domain, views=None):
        return {
            "type": "ir.actions.act_window",
            "name": title,
            "res_model": model_name,
            "view_mode": views or "list,form",
            "domain": domain,
            "target": "current",
        }

    def action_open_evaluations(self):
        self.ensure_one()
        return self._action_for_model(
            "agx.evaluation", _("Farm Evaluations"), self._get_eval_domain()
        )

    def action_open_batches(self):
        self.ensure_one()
        return self._action_for_model(
            "agx.batch", _("Production Batches"), self._get_batch_domain()
        )

    def action_open_shipments(self):
        self.ensure_one()
        return self._action_for_model(
            "agx.shipment", _("Export Shipments"), self._get_shipment_domain()
        )

    def action_open_profitability(self):
        self.ensure_one()
        return self._action_for_model(
            "agx.shipment",
            _("Shipment Profitability Analysis"),
            self._get_shipment_domain(),
            views="graph,pivot",
        )

    def action_open_incoming_receipts(self):
        self.ensure_one()
        return self._action_for_model(
            "stock.picking",
            _("Pending Incoming Receipts"),
            self._get_pending_receipt_domain(),
        )

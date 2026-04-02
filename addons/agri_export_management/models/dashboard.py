from collections import Counter

from odoo import _, api, fields, models


class AgxDashboard(models.Model):
    _name = "agx.dashboard"
    _description = "Agricultural Export Dashboard"
    _order = "company_id"

    _sql_constraints = [
        ("agx_dashboard_company_unique", "unique(company_id)", "Only one dashboard record is allowed per company."),
    ]

    name = fields.Char(compute="_compute_name")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", readonly=True)
    date_from = fields.Date(default=lambda self: fields.Date.context_today(self).replace(day=1), required=True)
    date_to = fields.Date(default=fields.Date.context_today, required=True)

    open_evaluations_count = fields.Integer(compute="_compute_metrics")
    approved_evaluations_count = fields.Integer(compute="_compute_metrics")
    pending_purchases_count = fields.Integer(compute="_compute_metrics")
    raw_material_received_count = fields.Integer(compute="_compute_metrics")
    active_batches_count = fields.Integer(compute="_compute_metrics")
    pending_shipments_count = fields.Integer(compute="_compute_metrics")
    reserved_lots_qty = fields.Float(compute="_compute_metrics")
    shipped_shipments_count = fields.Integer(compute="_compute_metrics")

    logistics_cost_period = fields.Monetary(currency_field="currency_id", compute="_compute_metrics")
    revenue_period = fields.Monetary(currency_field="currency_id", compute="_compute_metrics")
    gross_profit_period = fields.Monetary(currency_field="currency_id", compute="_compute_metrics")
    margin_pct_period = fields.Float(compute="_compute_metrics", digits=(16, 2))

    top_destination_name = fields.Char(compute="_compute_metrics")
    top_customer_name = fields.Char(compute="_compute_metrics")
    highest_cost_shipment_name = fields.Char(compute="_compute_metrics")
    highest_margin_shipment_name = fields.Char(compute="_compute_metrics")

    @api.depends("company_id")
    def _compute_name(self):
        for rec in self:
            rec.name = _("Agricultural Export Dashboard")

    def _get_eval_domain(self):
        self.ensure_one()
        return [
            ("company_id", "=", self.company_id.id),
            ("evaluation_date", ">=", self.date_from),
            ("evaluation_date", "<=", self.date_to),
        ]

    def _get_shipment_domain(self):
        self.ensure_one()
        return [
            ("company_id", "=", self.company_id.id),
            ("shipment_date", ">=", self.date_from),
            ("shipment_date", "<=", self.date_to),
        ]

    def _get_batch_domain(self):
        self.ensure_one()
        return [
            ("company_id", "=", self.company_id.id),
            ("batch_date", ">=", self.date_from),
            ("batch_date", "<=", self.date_to),
        ]

    def _get_receipt_domain(self):
        self.ensure_one()
        return [
            ("company_id", "=", self.company_id.id),
            ("picking_type_id.code", "=", "incoming"),
            ("date_done", "!=", False),
            ("date_done", ">=", self.date_from),
            ("date_done", "<=", f"{self.date_to} 23:59:59"),
            ("state", "=", "done"),
        ]

    @api.depends("company_id", "date_from", "date_to")
    def _compute_metrics(self):
        Evaluation = self.env["agx.evaluation"]
        Shipment = self.env["agx.shipment"]
        Batch = self.env["agx.batch"]
        Picking = self.env["stock.picking"]
        for rec in self:
            eval_domain = rec._get_eval_domain()
            shipment_domain = rec._get_shipment_domain()
            batch_domain = rec._get_batch_domain()
            receipt_domain = rec._get_receipt_domain()

            rec.open_evaluations_count = Evaluation.search_count(eval_domain + [("state", "=", "draft")])
            rec.approved_evaluations_count = Evaluation.search_count(eval_domain + [("state", "=", "approved")])
            rec.pending_purchases_count = Evaluation.search_count(eval_domain + [("state", "=", "approved"), ("po_id", "=", False)])
            rec.raw_material_received_count = Picking.search_count(receipt_domain)
            rec.active_batches_count = Batch.search_count(batch_domain + [("state", "in", ["draft", "in_progress"])])
            rec.pending_shipments_count = Shipment.search_count(shipment_domain + [("state", "in", ["draft", "reserved"])])

            shipments = Shipment.search(shipment_domain)
            rec.reserved_lots_qty = sum(shipments.mapped("lot_line_ids.reserved_qty"))
            rec.shipped_shipments_count = len(shipments.filtered(lambda s: s.state == "shipped"))
            rec.logistics_cost_period = sum(shipments.mapped("total_logistics_cost"))
            rec.revenue_period = sum(shipments.mapped("revenue_amount"))
            rec.gross_profit_period = sum(shipments.mapped("gross_profit_amount"))
            rec.margin_pct_period = (rec.gross_profit_period / rec.revenue_period * 100.0) if rec.revenue_period else 0.0

            destinations = Counter(shipments.filtered(lambda s: s.destination_id).mapped("destination_id.name"))
            customers = Counter(shipments.filtered(lambda s: s.customer_id).mapped("customer_id.display_name"))
            rec.top_destination_name = destinations.most_common(1)[0][0] if destinations else False
            rec.top_customer_name = customers.most_common(1)[0][0] if customers else False

            highest_cost = shipments.sorted(lambda s: s.total_logistics_cost, reverse=True)[:1]
            highest_margin = shipments.filtered(lambda s: s.revenue_amount).sorted(lambda s: s.gross_margin_pct, reverse=True)[:1]
            rec.highest_cost_shipment_name = highest_cost.name if highest_cost else False
            rec.highest_margin_shipment_name = highest_margin.name if highest_margin else False

    @api.model
    def action_open_dashboard(self):
        dashboard = self.search([("company_id", "=", self.env.company.id)], limit=1)
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

    def action_refresh_dashboard(self):
        self.ensure_one()
        return self.action_open_dashboard()

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
        return self._action_for_model("agx.evaluation", _("Farm Evaluations"), self._get_eval_domain())

    def action_open_batches(self):
        self.ensure_one()
        return self._action_for_model("agx.batch", _("Production Batches"), self._get_batch_domain())

    def action_open_shipments(self):
        self.ensure_one()
        return self._action_for_model("agx.shipment", _("Shipments"), self._get_shipment_domain())

    def action_open_profitability(self):
        self.ensure_one()
        return self._action_for_model("agx.shipment", _("Shipment Profitability Analysis"), self._get_shipment_domain(), views="graph,pivot,list,form")

    def action_open_incoming_receipts(self):
        self.ensure_one()
        return self._action_for_model("stock.picking", _("Incoming Receipts"), self._get_receipt_domain())

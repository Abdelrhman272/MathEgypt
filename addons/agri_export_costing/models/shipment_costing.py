# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ExportShipment(models.Model):
    _inherit = "export.shipment"

    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    container_count = fields.Integer(
        string="Container Count",
        default=1,
        tracking=True,
        help="Used for cost per container calculations. Keep 1 for the standard workflow.",
    )
    logistics_cost_line_ids = fields.One2many(
        "export.shipment.cost.line",
        "shipment_id",
        string="Logistics Costs",
        copy=True,
    )
    logistics_total_cost = fields.Monetary(
        string="Total Logistics Cost",
        currency_field="currency_id",
        compute="_compute_profitability_totals",
        store=True,
    )
    logistics_cost_per_container = fields.Monetary(
        string="Cost / Container",
        currency_field="currency_id",
        compute="_compute_profitability_totals",
        store=True,
    )
    revenue_amount = fields.Monetary(
        string="Revenue",
        currency_field="currency_id",
        compute="_compute_profitability_totals",
        store=True,
        help="Taken from the linked Sale Order untaxed amount.",
    )
    gross_profit_amount = fields.Monetary(
        string="Gross Profit",
        currency_field="currency_id",
        compute="_compute_profitability_totals",
        store=True,
    )
    gross_margin_pct = fields.Float(
        string="Gross Margin %",
        compute="_compute_profitability_totals",
        store=True,
        digits=(16, 2),
    )

    @api.depends(
        "logistics_cost_line_ids.amount",
        "container_count",
        "sale_order_id.amount_untaxed",
    )
    def _compute_profitability_totals(self):
        for rec in self:
            total_cost = sum(rec.logistics_cost_line_ids.mapped("amount"))
            revenue = rec.sale_order_id.amount_untaxed if rec.sale_order_id else 0.0
            rec.logistics_total_cost = total_cost
            rec.revenue_amount = revenue
            rec.logistics_cost_per_container = total_cost / rec.container_count if rec.container_count else 0.0
            rec.gross_profit_amount = revenue - total_cost
            rec.gross_margin_pct = (rec.gross_profit_amount / revenue * 100.0) if revenue else 0.0


class ExportShipmentLine(models.Model):
    _inherit = "export.shipment.line"

    carton_qty = fields.Float(
        string="Cartons",
        default=0.0,
        help="Used when allocating logistics costs per carton.",
    )
    net_weight = fields.Float(
        string="Net Weight",
        default=0.0,
        help="Used when allocating logistics costs by net weight.",
    )
    gross_weight = fields.Float(
        string="Gross Weight",
        default=0.0,
        help="Used when allocating logistics costs by gross weight.",
    )
    allocated_logistics_cost = fields.Monetary(
        string="Allocated Logistics Cost",
        currency_field="currency_id",
        compute="_compute_allocated_logistics_cost",
        store=True,
    )
    logistics_cost_per_carton = fields.Monetary(
        string="Cost / Carton",
        currency_field="currency_id",
        compute="_compute_allocated_logistics_cost",
        store=True,
    )
    logistics_cost_per_qty = fields.Monetary(
        string="Cost / Qty",
        currency_field="currency_id",
        compute="_compute_allocated_logistics_cost",
        store=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="shipment_id.currency_id",
        store=True,
        readonly=True,
    )

    @api.depends(
        "shipment_id.logistics_cost_line_ids.amount",
        "shipment_id.logistics_cost_line_ids.allocation_basis",
        "shipment_id.line_ids.product_uom_qty",
        "shipment_id.line_ids.carton_qty",
        "shipment_id.line_ids.net_weight",
        "shipment_id.line_ids.gross_weight",
    )
    def _compute_allocated_logistics_cost(self):
        for line in self:
            total_allocated = 0.0
            shipment = line.shipment_id
            for cost_line in shipment.logistics_cost_line_ids:
                basis = cost_line.allocation_basis or "qty"
                lines = shipment.line_ids
                if basis == "carton":
                    denominator = sum(lines.mapped("carton_qty"))
                    numerator = line.carton_qty
                elif basis == "net_weight":
                    denominator = sum(lines.mapped("net_weight"))
                    numerator = line.net_weight
                elif basis == "gross_weight":
                    denominator = sum(lines.mapped("gross_weight"))
                    numerator = line.gross_weight
                elif basis == "equal":
                    denominator = len(lines)
                    numerator = 1.0 if line.id else 0.0
                else:
                    denominator = sum(lines.mapped("product_uom_qty"))
                    numerator = line.product_uom_qty

                if denominator:
                    total_allocated += cost_line.amount * (numerator / denominator)

            line.allocated_logistics_cost = total_allocated
            line.logistics_cost_per_carton = total_allocated / line.carton_qty if line.carton_qty else 0.0
            line.logistics_cost_per_qty = total_allocated / line.product_uom_qty if line.product_uom_qty else 0.0


class ExportShipmentCostLine(models.Model):
    _name = "export.shipment.cost.line"
    _description = "Export Shipment Cost Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    shipment_id = fields.Many2one(
        "export.shipment",
        required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        "res.company",
        related="shipment_id.company_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="shipment_id.currency_id",
        store=True,
        readonly=True,
    )
    name = fields.Char(required=True)
    cost_type = fields.Selection(
        [
            ("inland", "Inland Transport"),
            ("port", "Port Charges"),
            ("ocean", "Ocean Freight"),
            ("customs", "Customs Clearance"),
            ("other", "Other"),
        ],
        required=True,
        default="other",
    )
    allocation_basis = fields.Selection(
        [
            ("qty", "By Quantity"),
            ("carton", "By Cartons"),
            ("net_weight", "By Net Weight"),
            ("gross_weight", "By Gross Weight"),
            ("equal", "Equal Share"),
        ],
        string="Allocation Basis",
        required=True,
        default="qty",
    )
    amount = fields.Monetary(required=True, currency_field="currency_id")
    note = fields.Char()

    @api.constrains("amount")
    def _check_amount_positive(self):
        for rec in self:
            if rec.amount < 0:
                raise ValidationError(_("Logistics cost amount cannot be negative."))

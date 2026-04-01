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
    manual_logistics_total_cost = fields.Monetary(
        string="Manual Logistics Cost",
        currency_field="currency_id",
        compute="_compute_profitability_totals",
        store=True,
    )
    actual_logistics_total_cost = fields.Monetary(
        string="Actual Logistics Cost",
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
    costing_status = fields.Selection(
        [
            ("manual", "Manual"),
            ("partial_actual", "Partial Actual"),
            ("full_actual", "Full Actual"),
        ],
        string="Costing Status",
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
        "logistics_cost_line_ids.cost_source_type",
        "logistics_cost_line_ids.effective_amount",
        "container_count",
        "sale_order_id.amount_untaxed",
    )
    def _compute_profitability_totals(self):
        for rec in self:
            total_cost = sum(rec.logistics_cost_line_ids.mapped("effective_amount"))
            manual_cost = sum(
                rec.logistics_cost_line_ids.filtered(lambda l: l.cost_source_type == "manual").mapped("effective_amount")
            )
            actual_cost = sum(
                rec.logistics_cost_line_ids.filtered(lambda l: l.cost_source_type != "manual").mapped("effective_amount")
            )
            revenue = rec.sale_order_id.amount_untaxed if rec.sale_order_id else 0.0

            rec.logistics_total_cost = total_cost
            rec.manual_logistics_total_cost = manual_cost
            rec.actual_logistics_total_cost = actual_cost
            rec.revenue_amount = revenue
            rec.logistics_cost_per_container = total_cost / rec.container_count if rec.container_count else 0.0
            rec.gross_profit_amount = revenue - total_cost
            rec.gross_margin_pct = (rec.gross_profit_amount / revenue * 100.0) if revenue else 0.0

            lines = rec.logistics_cost_line_ids.filtered(lambda l: l.effective_amount or l.cost_source_type == "manual")
            if not lines:
                rec.costing_status = "manual"
            elif all(line.cost_source_type == "manual" for line in lines):
                rec.costing_status = "manual"
            elif all(line.cost_source_type != "manual" for line in lines):
                rec.costing_status = "full_actual"
            else:
                rec.costing_status = "partial_actual"


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
        "shipment_id.logistics_cost_line_ids.effective_amount",
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
                    total_allocated += cost_line.effective_amount * (numerator / denominator)

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
    cost_source_type = fields.Selection(
        [
            ("manual", "Manual"),
            ("vendor_bill", "Vendor Bill"),
            ("vendor_bill_line", "Vendor Bill Line"),
            ("landed_cost", "Landed Cost"),
        ],
        string="Cost Source",
        required=True,
        default="manual",
    )
    vendor_bill_id = fields.Many2one(
        "account.move",
        string="Vendor Bill",
        domain="[(\"move_type\", \"in\", [\"in_invoice\", \"in_refund\"])]",
    )
    vendor_bill_line_id = fields.Many2one(
        "account.move.line",
        string="Vendor Bill Line",
        domain="[(\"display_type\", \"=\", False)]",
    )
    landed_cost_id = fields.Many2one(
        "stock.landed.cost",
        string="Landed Cost",
    )
    amount = fields.Monetary(
        string="Manual Amount",
        currency_field="currency_id",
        default=0.0,
        help="Used when Cost Source is Manual.",
    )
    source_amount = fields.Monetary(
        string="Source Amount",
        currency_field="currency_id",
        compute="_compute_source_amounts",
        store=True,
        readonly=True,
    )
    effective_amount = fields.Monetary(
        string="Effective Amount",
        currency_field="currency_id",
        compute="_compute_source_amounts",
        store=True,
        readonly=True,
        help="Amount actually used in shipment profitability and allocation.",
    )
    note = fields.Char()

    @api.depends(
        "cost_source_type",
        "amount",
        "vendor_bill_id.invoice_line_ids.price_subtotal",
        "vendor_bill_line_id.price_subtotal",
        "landed_cost_id.cost_lines.price_unit",
    )
    def _compute_source_amounts(self):
        for rec in self:
            source_amount = 0.0

            if rec.cost_source_type == "vendor_bill_line":
                source_amount = rec.vendor_bill_line_id.price_subtotal if rec.vendor_bill_line_id else 0.0
            elif rec.cost_source_type == "vendor_bill":
                bill_lines = rec.vendor_bill_id.invoice_line_ids.filtered(lambda l: not l.display_type)
                source_amount = sum(bill_lines.mapped("price_subtotal")) if rec.vendor_bill_id else 0.0
            elif rec.cost_source_type == "landed_cost":
                source_amount = rec._get_landed_cost_total()

            rec.source_amount = source_amount
            rec.effective_amount = rec.amount if rec.cost_source_type == "manual" else source_amount

    def _get_landed_cost_total(self):
        self.ensure_one()
        landed_cost = self.landed_cost_id
        if not landed_cost:
            return 0.0

        if "cost_lines" in landed_cost._fields:
            try:
                return sum(landed_cost.cost_lines.mapped("price_unit"))
            except Exception:
                pass

        if "amount_total" in landed_cost._fields:
            try:
                return landed_cost.amount_total or 0.0
            except Exception:
                pass

        if "total_amount" in landed_cost._fields:
            try:
                return landed_cost.total_amount or 0.0
            except Exception:
                pass

        if "valuation_adjustment_lines" in landed_cost._fields:
            try:
                return sum(landed_cost.valuation_adjustment_lines.mapped("additional_landed_cost"))
            except Exception:
                pass

        return 0.0

    @api.onchange("cost_source_type")
    def _onchange_cost_source_type(self):
        for rec in self:
            if rec.cost_source_type == "manual":
                rec.vendor_bill_id = False
                rec.vendor_bill_line_id = False
                rec.landed_cost_id = False
            elif rec.cost_source_type == "vendor_bill":
                rec.vendor_bill_line_id = False
                rec.landed_cost_id = False
            elif rec.cost_source_type == "vendor_bill_line":
                rec.landed_cost_id = False
            elif rec.cost_source_type == "landed_cost":
                rec.vendor_bill_id = False
                rec.vendor_bill_line_id = False

    @api.onchange("vendor_bill_line_id")
    def _onchange_vendor_bill_line_id(self):
        for rec in self:
            if rec.vendor_bill_line_id:
                rec.vendor_bill_id = rec.vendor_bill_line_id.move_id

    @api.constrains("amount", "effective_amount")
    def _check_amount_positive(self):
        for rec in self:
            if rec.amount < 0:
                raise ValidationError(_("Manual logistics cost amount cannot be negative."))
            if rec.effective_amount < 0:
                raise ValidationError(_("Effective logistics cost amount cannot be negative."))

    @api.constrains("cost_source_type", "vendor_bill_id", "vendor_bill_line_id", "landed_cost_id")
    def _check_source_reference(self):
        for rec in self:
            if rec.cost_source_type == "vendor_bill" and not rec.vendor_bill_id:
                raise ValidationError(_("Please select a Vendor Bill for this logistics cost line."))
            if rec.cost_source_type == "vendor_bill_line" and not rec.vendor_bill_line_id:
                raise ValidationError(_("Please select a Vendor Bill Line for this logistics cost line."))
            if rec.cost_source_type == "landed_cost" and not rec.landed_cost_id:
                raise ValidationError(_("Please select a Landed Cost for this logistics cost line."))

    @api.constrains("vendor_bill_id", "vendor_bill_line_id", "landed_cost_id", "company_id")
    def _check_company_consistency(self):
        for rec in self:
            if rec.vendor_bill_id and rec.vendor_bill_id.company_id != rec.company_id:
                raise ValidationError(_("Vendor Bill company must match Shipment company."))
            if rec.vendor_bill_line_id and rec.vendor_bill_line_id.company_id != rec.company_id:
                raise ValidationError(_("Vendor Bill Line company must match Shipment company."))
            if rec.landed_cost_id and rec.landed_cost_id.company_id != rec.company_id:
                raise ValidationError(_("Landed Cost company must match Shipment company."))
            if rec.vendor_bill_line_id and rec.vendor_bill_id and rec.vendor_bill_line_id.move_id != rec.vendor_bill_id:
                raise ValidationError(_("Vendor Bill Line must belong to the selected Vendor Bill."))

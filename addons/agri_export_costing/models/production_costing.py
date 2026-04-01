# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AgriProductionBatch(models.Model):
    _inherit = "agri.production.batch"

    company_id = fields.Many2one(
        "res.company",
        related="mrp_production_id.company_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    costing_method = fields.Selection(
        [("relative_sales_value", "Relative Sales Value")],
        default="relative_sales_value",
        required=True,
    )
    raw_material_cost_amount = fields.Monetary(
        string="Manual Raw Material Cost",
        currency_field="currency_id",
        default=0.0,
        help="Fallback manual raw material cost if actual valuation is not available.",
    )
    operation_cost_amount = fields.Monetary(
        string="Manual Operation Cost",
        currency_field="currency_id",
        default=0.0,
        help="Fallback manual processing / grading / packing cost.",
    )
    other_cost_amount = fields.Monetary(
        string="Manual Other Cost",
        currency_field="currency_id",
        default=0.0,
    )
    actual_raw_material_cost = fields.Monetary(
        string="Actual Raw Material Cost",
        currency_field="currency_id",
        readonly=True,
        copy=False,
        default=0.0,
    )
    actual_operation_cost = fields.Monetary(
        string="Actual Operation Cost",
        currency_field="currency_id",
        readonly=True,
        copy=False,
        default=0.0,
    )
    actual_other_cost = fields.Monetary(
        string="Actual Other Cost",
        currency_field="currency_id",
        readonly=True,
        copy=False,
        default=0.0,
    )
    actual_total_allocable_cost = fields.Monetary(
        string="Actual Allocable Cost",
        currency_field="currency_id",
        compute="_compute_costing_totals",
        store=True,
    )
    effective_raw_material_cost = fields.Monetary(
        string="Effective Raw Material Cost",
        currency_field="currency_id",
        compute="_compute_costing_totals",
        store=True,
    )
    effective_operation_cost = fields.Monetary(
        string="Effective Operation Cost",
        currency_field="currency_id",
        compute="_compute_costing_totals",
        store=True,
    )
    effective_other_cost = fields.Monetary(
        string="Effective Other Cost",
        currency_field="currency_id",
        compute="_compute_costing_totals",
        store=True,
    )
    total_allocable_cost = fields.Monetary(
        string="Effective Allocable Cost",
        currency_field="currency_id",
        compute="_compute_costing_totals",
        store=True,
    )
    total_relative_sales_value = fields.Monetary(
        string="Total Relative Sales Value",
        currency_field="currency_id",
        compute="_compute_costing_totals",
        store=True,
    )
    costing_status = fields.Selection(
        [
            ("manual", "Manual"),
            ("partial_actual", "Partial Actual"),
            ("full_actual", "Full Actual"),
        ],
        string="Costing Status",
        compute="_compute_costing_totals",
        store=True,
    )
    raw_costing_source = fields.Selection(
        [("manual", "Manual"), ("actual", "Actual")],
        string="Raw Cost Source",
        compute="_compute_costing_totals",
        store=True,
    )
    operation_costing_source = fields.Selection(
        [("manual", "Manual"), ("actual", "Actual")],
        string="Operation Cost Source",
        compute="_compute_costing_totals",
        store=True,
    )
    other_costing_source = fields.Selection(
        [("manual", "Manual"), ("actual", "Actual")],
        string="Other Cost Source",
        compute="_compute_costing_totals",
        store=True,
    )
    actual_cost_last_refresh = fields.Datetime(
        string="Actual Cost Last Refresh",
        readonly=True,
        copy=False,
    )

    @api.depends(
        "raw_material_cost_amount",
        "operation_cost_amount",
        "other_cost_amount",
        "actual_raw_material_cost",
        "actual_operation_cost",
        "actual_other_cost",
        "output_line_ids.sales_value",
        "mrp_production_id",
        "mrp_production_id.move_raw_ids.state",
    )
    def _compute_costing_totals(self):
        for rec in self:
            rec.actual_total_allocable_cost = (
                (rec.actual_raw_material_cost or 0.0)
                + (rec.actual_operation_cost or 0.0)
                + (rec.actual_other_cost or 0.0)
            )

            rec.raw_costing_source = "actual" if rec.actual_raw_material_cost else "manual"
            rec.operation_costing_source = "actual" if rec.actual_operation_cost else "manual"
            rec.other_costing_source = "actual" if rec.actual_other_cost else "manual"

            rec.effective_raw_material_cost = (
                rec.actual_raw_material_cost if rec.actual_raw_material_cost else (rec.raw_material_cost_amount or 0.0)
            )
            rec.effective_operation_cost = (
                rec.actual_operation_cost if rec.actual_operation_cost else (rec.operation_cost_amount or 0.0)
            )
            rec.effective_other_cost = (
                rec.actual_other_cost if rec.actual_other_cost else (rec.other_cost_amount or 0.0)
            )
            rec.total_allocable_cost = (
                (rec.effective_raw_material_cost or 0.0)
                + (rec.effective_operation_cost or 0.0)
                + (rec.effective_other_cost or 0.0)
            )
            rec.total_relative_sales_value = sum(rec.output_line_ids.mapped("sales_value"))

            if not rec.mrp_production_id:
                rec.costing_status = "manual"
                continue

            has_any_actual = bool(
                rec.actual_raw_material_cost or rec.actual_operation_cost or rec.actual_other_cost
            )
            raw_needed = bool(rec.mrp_production_id.move_raw_ids)
            op_needed = bool(getattr(rec.mrp_production_id, "workorder_ids", False))
            raw_ready = (not raw_needed) or bool(rec.actual_raw_material_cost)
            op_ready = (not op_needed) or bool(rec.actual_operation_cost)

            if has_any_actual and raw_ready and op_ready:
                rec.costing_status = "full_actual"
            elif has_any_actual:
                rec.costing_status = "partial_actual"
            else:
                rec.costing_status = "manual"

    def _get_move_done_qty(self, move):
        for field_name in ("quantity", "quantity_done", "product_uom_qty"):
            if field_name in move._fields:
                return move[field_name] or 0.0
        return 0.0

    def _get_move_actual_cost(self, move):
        value = 0.0
        if "stock_valuation_layer_ids" in move._fields:
            svls = move.stock_valuation_layer_ids
            if svls:
                value = abs(sum(svls.mapped("value")))
        if value:
            return value

        qty = self._get_move_done_qty(move)
        product = move.product_id
        return abs(qty * (product.standard_price or 0.0))

    def _get_actual_raw_material_cost(self):
        self.ensure_one()
        mo = self.mrp_production_id
        if not mo:
            return 0.0

        raw_moves = mo.move_raw_ids.filtered(lambda m: m.state == "done")
        return sum(self._get_move_actual_cost(move) for move in raw_moves)

    def _get_workorder_duration_minutes(self, workorder):
        for field_name in ("duration", "duration_expected"):
            if field_name in workorder._fields and workorder[field_name]:
                return workorder[field_name]
        return 0.0

    def _get_workcenter_hourly_cost(self, workcenter):
        for field_name in (
            "costs_hour",
            "costs_hour_account",
            "costs_hour_employee",
            "costs_hour_operation",
        ):
            if field_name in workcenter._fields and workcenter[field_name]:
                return workcenter[field_name]
        return 0.0

    def _get_actual_operation_cost(self):
        self.ensure_one()
        mo = self.mrp_production_id
        if not mo or "workorder_ids" not in mo._fields:
            return 0.0

        operation_cost = 0.0
        for workorder in mo.workorder_ids:
            duration_minutes = self._get_workorder_duration_minutes(workorder)
            hourly_cost = self._get_workcenter_hourly_cost(workorder.workcenter_id)
            operation_cost += (duration_minutes / 60.0) * hourly_cost
        return operation_cost

    def action_refresh_actual_costs(self):
        for rec in self:
            rec.write({
                "actual_raw_material_cost": rec._get_actual_raw_material_cost(),
                "actual_operation_cost": rec._get_actual_operation_cost(),
                "actual_other_cost": 0.0,
                "actual_cost_last_refresh": fields.Datetime.now(),
            })
        return True

    def action_sync_from_mo(self):
        parent_method = getattr(super(AgriProductionBatch, self), "action_sync_from_mo", None)
        res = parent_method() if parent_method else True
        self.action_refresh_actual_costs()
        return res


class AgriProductionOutput(models.Model):
    _inherit = "agri.production.output"

    currency_id = fields.Many2one(
        "res.currency",
        related="batch_id.currency_id",
        store=True,
        readonly=True,
    )
    sales_price_unit = fields.Monetary(
        string="Sales Price / Unit",
        currency_field="currency_id",
        default=0.0,
    )
    sales_value = fields.Monetary(
        string="Sales Value",
        currency_field="currency_id",
        compute="_compute_sales_value",
        store=True,
    )
    relative_sales_ratio = fields.Float(
        string="Relative Sales Ratio",
        compute="_compute_allocated_cost_fields",
        store=True,
        digits=(16, 6),
    )
    allocated_cost = fields.Monetary(
        string="Allocated Cost",
        currency_field="currency_id",
        compute="_compute_allocated_cost_fields",
        store=True,
    )
    cost_per_unit = fields.Monetary(
        string="Cost / Unit",
        currency_field="currency_id",
        compute="_compute_allocated_cost_fields",
        store=True,
    )

    @api.depends("qty", "sales_price_unit")
    def _compute_sales_value(self):
        for line in self:
            line.sales_value = (line.qty or 0.0) * (line.sales_price_unit or 0.0)

    @api.depends(
        "qty",
        "sales_value",
        "batch_id.total_allocable_cost",
        "batch_id.total_relative_sales_value",
    )
    def _compute_allocated_cost_fields(self):
        for line in self:
            total_sales_value = line.batch_id.total_relative_sales_value or 0.0
            ratio = (line.sales_value / total_sales_value) if total_sales_value else 0.0
            allocated_cost = (line.batch_id.total_allocable_cost or 0.0) * ratio

            line.relative_sales_ratio = ratio
            line.allocated_cost = allocated_cost
            line.cost_per_unit = (allocated_cost / line.qty) if line.qty else 0.0

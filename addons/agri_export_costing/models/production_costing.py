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
        string="Raw Material Cost",
        currency_field="currency_id",
        default=0.0,
        help="Optional. Include here only if you want joint raw cost inside the cost share.",
    )
    operation_cost_amount = fields.Monetary(
        string="Operation Cost",
        currency_field="currency_id",
        default=0.0,
        help="Primary processing / grading / packing cost to distribute.",
    )
    other_cost_amount = fields.Monetary(
        string="Other Cost",
        currency_field="currency_id",
        default=0.0,
    )
    total_allocable_cost = fields.Monetary(
        string="Allocable Cost",
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

    @api.depends(
        "raw_material_cost_amount",
        "operation_cost_amount",
        "other_cost_amount",
        "output_line_ids.sales_value",
    )
    def _compute_costing_totals(self):
        for rec in self:
            rec.total_allocable_cost = (
                (rec.raw_material_cost_amount or 0.0)
                + (rec.operation_cost_amount or 0.0)
                + (rec.other_cost_amount or 0.0)
            )
            rec.total_relative_sales_value = sum(rec.output_line_ids.mapped("sales_value"))


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

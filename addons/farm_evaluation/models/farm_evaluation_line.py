# -*- coding: utf-8 -*-
import re
from odoo import models, fields, api

# supports: "G-A" / "g-a" inside product name like "Orange G-A / S-36"
GRADE_RE = re.compile(r"\bG-([A-D])\b", re.IGNORECASE)


class FarmEvaluationLine(models.Model):
    _inherit = "farm.evaluation.line"

    actual_qty = fields.Float(string="Actual Qty", compute="_compute_actuals", store=True)
    variance_qty = fields.Float(string="Variance", compute="_compute_actuals", store=True)
    achievement_percent = fields.Float(string="Achievement %", compute="_compute_actuals", store=True)

    @api.depends("evaluation_id.total_expected_qty", "expected_qty", "grade", "evaluation_id.uom_id")
    def _compute_actuals(self):
        MrpProduction = self.env["mrp.production"]

        for line in self:
            evaluation = line.evaluation_id
            if not evaluation:
                line.actual_qty = 0.0
                line.variance_qty = 0.0
                line.achievement_percent = 0.0
                continue

            productions = MrpProduction.search([
                ("farm_evaluation_id", "=", evaluation.id),
                ("state", "=", "done"),
            ])

            actual = 0.0
            eval_uom = line.uom_id  # evaluation uom

            for mo in productions:
                for move in mo.move_byproduct_ids:
                    product = move.product_id
                    if not product:
                        continue

                    # 1) Prefer explicit farm_grade if exists on template (won't crash if not exists)
                    tmpl = product.product_tmpl_id
                    prod_grade = ""
                    if hasattr(tmpl, "farm_grade") and tmpl.farm_grade:
                        prod_grade = (tmpl.farm_grade or "").strip().upper()

                    # 2) Fallback: infer from product name like "Orange G-A / S-36"
                    if not prod_grade:
                        name = product.display_name or tmpl.display_name or ""
                        m = GRADE_RE.search(name)
                        if m:
                            prod_grade = (m.group(1) or "").strip().upper()

                    # no grade => skip
                    if not prod_grade:
                        continue

                    if prod_grade != (line.grade or "").strip().upper():
                        continue

                    # ✅ Odoo 19: done qty is on move lines
                    qty = sum(move.move_line_ids.mapped("qty_done")) or 0.0

                    # fallback (rare): if no move lines, use planned qty
                    if not qty:
                        qty = move.product_uom_qty or 0.0

                    # Convert move UoM -> evaluation UoM (kg -> ton, etc.)
                    if eval_uom and move.product_uom and move.product_uom != eval_uom:
                        qty = move.product_uom._compute_quantity(qty, eval_uom)

                    actual += qty

            line.actual_qty = actual
            line.variance_qty = (line.actual_qty or 0.0) - (line.expected_qty or 0.0)
            line.achievement_percent = (line.actual_qty / line.expected_qty * 100.0) if line.expected_qty else 0.0

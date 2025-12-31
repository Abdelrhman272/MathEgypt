from odoo import models, fields, api

class FarmEvaluationLine(models.Model):
    _inherit = "farm.evaluation.line"

    actual_qty = fields.Float(
        string="Actual Qty",
        compute="_compute_actual_qty",
        store=True,
    )

    variance_qty = fields.Float(
        string="Variance",
        compute="_compute_variance_qty",
        store=True,
    )

    achievement_percent = fields.Float(
        string="Achievement %",
        compute="_compute_achievement_percent",
        store=True,
    )

    def _compute_actual_qty(self):
        for line in self:
            actual = 0.0
            evaluation = line.evaluation_id
            if not evaluation:
                line.actual_qty = 0.0
                continue

            productions = self.env["mrp.production"].search([
                ("farm_evaluation_id", "=", evaluation.id),
                ("state", "=", "done"),
            ])

            for prod in productions:
                for move in prod.move_byproduct_ids:
                    product = move.product_id
                    if not product:
                        continue

                    # 🔑 Grade taken from product attribute
                    grade_attr = product.product_template_attribute_value_ids.filtered(
                        lambda v: v.attribute_id.name.lower() == "grade"
                    )
                    if grade_attr and grade_attr[0].name == line.grade:
                        actual += move.quantity_done

            line.actual_qty = actual

    def _compute_variance_qty(self):
        for line in self:
            line.variance_qty = line.actual_qty - line.expected_qty

    def _compute_achievement_percent(self):
        for line in self:
            if line.expected_qty:
                line.achievement_percent = (line.actual_qty / line.expected_qty) * 100
            else:
                line.achievement_percent = 0.0

# -*- coding: utf-8 -*-
"""
wizards.py — Transient Wizards for Agricultural Export Management
=================================================================
Contains four wizards:

1. AgxReserveLotsWizard
   Manual lot reservation for a single lot on a shipment.
   Complements the automatic ``action_reserve`` flow on agx.shipment.

2. AgxImportCostsWizard
   Imports logistics costs from a Vendor Bill or Landed Cost onto a
   shipment, creating cost lines automatically.

3. AgxTraceabilityWizard
   Full traceability report: given a shipment, batch, or lot, renders
   an HTML report showing the complete chain from farm to customer.

4. AgxInitProductAttributesWizard  ← NEW
   One-time configuration wizard that:
     a. Creates (or finds) two product attributes: 'Grade' and 'Size'.
     b. For every agx.grade record, creates the matching attribute value
        and links it via ``attribute_value_id``.
     c. For every agx.size record, does the same.
     d. Optionally adds both attributes to a selected finished-goods
        product template, generating all variants automatically.

   This wizard is the bridge between AGX master data and Odoo's product
   variant system, making Grade+Size combinations trackable in stock.
"""

from markupsafe import escape as html_escape

from odoo import _, fields, models
from odoo.exceptions import UserError


# ---------------------------------------------------------------------------
# 1. Reserve Lots Wizard
# ---------------------------------------------------------------------------
class AgxReserveLotsWizard(models.TransientModel):
    """Manual single-lot reservation wizard for a shipment.

    Used when the automatic ``action_reserve`` doesn't select the exact
    lot the user wants.  Validates that the requested quantity does not
    exceed the lot's effective available quantity before creating the
    reservation line.
    """

    _name = "agx.reserve.lots.wizard"
    _description = "Reserve Lots Wizard"

    shipment_id = fields.Many2one("agx.shipment", required=True)
    product_id = fields.Many2one("product.product")
    lot_id = fields.Many2one("stock.lot")
    available_qty = fields.Float(
        readonly=True,
        help="Current effective available quantity for the selected lot.",
    )
    reserve_qty = fields.Float(
        required=True,
        default=1.0,
        help="Quantity to reserve from this lot.",
    )

    def action_apply(self):
        """Validate and create the lot reservation line."""
        self.ensure_one()
        if not self.shipment_id or not self.product_id or not self.lot_id:
            raise UserError(
                _(
                    "Please select a shipment, product, and lot "
                    "before applying the reservation."
                )
            )
        effective_available = self.shipment_id._get_lot_effective_available_qty(
            lot_id=self.lot_id.id,
            product_id=self.product_id.id,
        )
        if self.reserve_qty <= 0:
            raise UserError(_("Reserve quantity must be greater than zero."))
        if self.reserve_qty > effective_available:
            raise UserError(
                _(
                    "Reserve quantity cannot exceed the currently available "
                    "quantity for the selected lot (%(available)s)."
                )
                % {"available": effective_available}
            )
        batch_output = self.env["agx.batch.output"].search(
            [
                ("lot_id", "=", self.lot_id.id),
                ("product_id", "=", self.product_id.id),
            ],
            limit=1,
        )
        self.env["agx.shipment.lot.line"].create(
            {
                "shipment_id": self.shipment_id.id,
                "product_id": self.product_id.id,
                "lot_id": self.lot_id.id,
                "batch_output_id": batch_output.id if batch_output else False,
                "available_qty": effective_available,
                "reserved_qty": self.reserve_qty,
            }
        )
        return {"type": "ir.actions.act_window_close"}


# ---------------------------------------------------------------------------
# 2. Import Costs Wizard
# ---------------------------------------------------------------------------
class AgxImportCostsWizard(models.TransientModel):
    """Imports logistics costs from a Vendor Bill or Landed Cost.

    Creates ``agx.shipment.cost.line`` records on the shipment, linking
    each bill line (or the whole bill/landed cost) to the appropriate
    cost type.  Duplicate detection prevents importing the same source
    twice.
    """

    _name = "agx.import.costs.wizard"
    _description = "Import Shipment Costs Wizard"

    shipment_id = fields.Many2one("agx.shipment", required=True)
    vendor_bill_id = fields.Many2one(
        "account.move",
        domain="[('move_type', '=', 'in_invoice')]",
        help="Import each bill line as a separate cost line.",
    )
    landed_cost_id = fields.Many2one(
        "stock.landed.cost",
        help="Import the landed cost total as a single cost line.",
    )

    def _get_default_cost_type(self):
        """Return the 'other' cost type, creating it if absent."""
        cost_type = self.env["agx.shipment.cost.type"].search(
            [("code", "=", "other")], limit=1
        )
        if not cost_type:
            cost_type = self.env["agx.shipment.cost.type"].search([], limit=1)
        if not cost_type:
            cost_type = self.env["agx.shipment.cost.type"].create(
                {
                    "name": "Other",
                    "code": "other",
                    "default_allocation_basis": (
                        self.shipment_id.company_id.agx_default_logistics_basis
                        or "qty"
                    ),
                }
            )
        return cost_type

    def action_apply(self):
        """Build cost lines from the selected bill or landed cost."""
        self.ensure_one()
        if not self.vendor_bill_id and not self.landed_cost_id:
            raise UserError(
                _("Please select a Vendor Bill or a Landed Cost to import.")
            )
        cost_type = self._get_default_cost_type()
        allocation_basis = (
            cost_type.default_allocation_basis
            or self.shipment_id.company_id.agx_default_logistics_basis
            or "qty"
        )
        cost_lines_to_create = []

        # -- Vendor Bill --------------------------------------------------
        if self.vendor_bill_id:
            invoice_lines = self.vendor_bill_id.invoice_line_ids.filtered(
                lambda l: not l.display_type
            )
            for bill_line in invoice_lines:
                already = self.shipment_id.cost_line_ids.filtered(
                    lambda l: l.vendor_bill_line_id == bill_line
                )
                if already:
                    continue
                cost_lines_to_create.append(
                    {
                        "name": (
                            bill_line.name
                            or bill_line.product_id.display_name
                            or self.vendor_bill_id.display_name
                        ),
                        "cost_type_id": cost_type.id,
                        "allocation_basis": allocation_basis,
                        "cost_source": "vendor_bill_line",
                        "vendor_bill_id": self.vendor_bill_id.id,
                        "vendor_bill_line_id": bill_line.id,
                        "note": _(
                            "Imported from Vendor Bill %s"
                        ) % self.vendor_bill_id.display_name,
                    }
                )
            # Fallback: import whole bill if no lines
            if not invoice_lines:
                already = self.shipment_id.cost_line_ids.filtered(
                    lambda l: l.vendor_bill_id == self.vendor_bill_id
                    and l.cost_source == "vendor_bill"
                )
                if not already:
                    cost_lines_to_create.append(
                        {
                            "name": self.vendor_bill_id.display_name,
                            "cost_type_id": cost_type.id,
                            "allocation_basis": allocation_basis,
                            "cost_source": "vendor_bill",
                            "vendor_bill_id": self.vendor_bill_id.id,
                            "note": _(
                                "Imported from Vendor Bill %s"
                            ) % self.vendor_bill_id.display_name,
                        }
                    )

        # -- Landed Cost --------------------------------------------------
        if self.landed_cost_id:
            already = self.shipment_id.cost_line_ids.filtered(
                lambda l: l.landed_cost_id == self.landed_cost_id
            )
            if not already:
                cost_lines_to_create.append(
                    {
                        "name": self.landed_cost_id.display_name,
                        "cost_type_id": cost_type.id,
                        "allocation_basis": allocation_basis,
                        "cost_source": "landed_cost",
                        "landed_cost_id": self.landed_cost_id.id,
                        "note": _(
                            "Imported from Landed Cost %s"
                        ) % self.landed_cost_id.display_name,
                    }
                )

        if cost_lines_to_create:
            self.shipment_id.write(
                {"cost_line_ids": [(0, 0, v) for v in cost_lines_to_create]}
            )
        return {"type": "ir.actions.act_window_close"}


# ---------------------------------------------------------------------------
# 3. Traceability Wizard
# ---------------------------------------------------------------------------
class AgxTraceabilityWizard(models.TransientModel):
    """Full traceability report rendered as HTML.

    Given any combination of shipment, batch, and/or lot, renders an
    HTML table showing:
      - Shipment → reserved lots → batch → evaluation → farm
      - Batch → inputs (lots) → outputs (lots, grade, size)
      - Lot → which batch produced it → which shipments reserved it

    The HTML is displayed directly in the wizard form using an Html field.
    """

    _name = "agx.traceability.wizard"
    _description = "Traceability Wizard"

    shipment_id = fields.Many2one("agx.shipment")
    batch_id = fields.Many2one("agx.batch")
    lot_id = fields.Many2one("stock.lot")
    result_html = fields.Html(readonly=True)

    # ------------------------------------------------------------------
    # HTML building helpers
    # ------------------------------------------------------------------
    def _fmt(self, value):
        """Escape a value for safe HTML rendering."""
        return html_escape(str(value or "-"))

    def _render_shipment_block(self, shipment):
        parts = [
            "<h3>Shipment: {}</h3>".format(self._fmt(shipment.display_name))
        ]
        parts.append("<ul>")
        parts.append(
            "<li>Customer: {}</li>".format(
                self._fmt(shipment.customer_id.display_name)
            )
        )
        parts.append(
            "<li>Destination: {}</li>".format(
                self._fmt(shipment.destination_id.display_name)
            )
        )
        parts.append(
            "<li>Season: {}</li>".format(
                self._fmt(
                    shipment.season_id.name if shipment.season_id else "-"
                )
            )
        )
        parts.append(
            "<li>Containers: {}</li>".format(
                self._fmt(shipment.container_count)
            )
        )
        parts.append(
            "<li>Total Reserved Qty: {}</li>".format(
                self._fmt(shipment.total_reserved_qty)
            )
        )
        parts.append("</ul>")
        if shipment.lot_line_ids:
            parts.append(
                "<h4>Reserved Lots</h4>"
                "<table class='table table-sm table-bordered'>"
                "<thead><tr>"
                "<th>Product</th><th>Grade</th><th>Size</th>"
                "<th>Lot</th><th>Reserved Qty</th><th>Batch</th>"
                "<th>Farm</th>"
                "</tr></thead><tbody>"
            )
            for lot_line in shipment.lot_line_ids:
                batch = lot_line.batch_output_id.batch_id
                evaluation = batch.evaluation_id if batch else False
                farm = evaluation.farm_partner_id if evaluation else False
                parts.append(
                    "<tr>"
                    "<td>{}</td><td>{}</td><td>{}</td>"
                    "<td>{}</td><td>{}</td><td>{}</td>"
                    "<td>{}</td>"
                    "</tr>".format(
                        self._fmt(lot_line.product_id.display_name),
                        self._fmt(
                            lot_line.batch_output_id.grade_id.name
                            if lot_line.batch_output_id
                            else ""
                        ),
                        self._fmt(
                            lot_line.batch_output_id.size_id.name
                            if lot_line.batch_output_id
                            else ""
                        ),
                        self._fmt(lot_line.lot_id.display_name),
                        self._fmt(lot_line.reserved_qty),
                        self._fmt(batch.display_name if batch else ""),
                        self._fmt(farm.name if farm else ""),
                    )
                )
            parts.append("</tbody></table>")
        return "".join(parts)

    def _render_batch_block(self, batch):
        parts = [
            "<h3>Batch: {}</h3>".format(self._fmt(batch.display_name))
        ]
        parts.append("<ul>")
        parts.append(
            "<li>Evaluation: {}</li>".format(
                self._fmt(batch.evaluation_id.display_name)
            )
        )
        parts.append(
            "<li>Season: {}</li>".format(
                self._fmt(batch.season_id.name if batch.season_id else "-")
            )
        )
        parts.append(
            "<li>Input Qty: {}</li>".format(self._fmt(batch.input_qty))
        )
        parts.append(
            "<li>Output Qty: {}</li>".format(self._fmt(batch.output_qty))
        )
        parts.append(
            "<li>Allocable Cost: {}</li>".format(
                self._fmt(batch.effective_allocable_cost)
            )
        )
        parts.append("</ul>")
        if batch.input_line_ids:
            parts.append(
                "<h4>Inputs</h4>"
                "<table class='table table-sm table-bordered'>"
                "<thead><tr><th>Product</th><th>Lot</th><th>Qty</th></tr></thead><tbody>"
            )
            for line in batch.input_line_ids:
                parts.append(
                    "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                        self._fmt(line.product_id.display_name),
                        self._fmt(line.lot_id.display_name),
                        self._fmt(line.qty),
                    )
                )
            parts.append("</tbody></table>")
        if batch.output_line_ids:
            parts.append(
                "<h4>Outputs</h4>"
                "<table class='table table-sm table-bordered'>"
                "<thead><tr>"
                "<th>Product</th><th>Lot</th><th>Grade</th>"
                "<th>Size</th><th>Qty</th><th>Cost/Unit</th>"
                "</tr></thead><tbody>"
            )
            for line in batch.output_line_ids:
                parts.append(
                    "<tr><td>{}</td><td>{}</td><td>{}</td>"
                    "<td>{}</td><td>{}</td><td>{}</td></tr>".format(
                        self._fmt(line.product_id.display_name),
                        self._fmt(line.lot_id.display_name),
                        self._fmt(line.grade_id.display_name),
                        self._fmt(line.size_id.display_name),
                        self._fmt(line.qty),
                        self._fmt(line.cost_per_unit),
                    )
                )
            parts.append("</tbody></table>")
        return "".join(parts)

    def _render_lot_block(self, lot):
        batch_outputs = self.env["agx.batch.output"].search(
            [("lot_id", "=", lot.id)]
        )
        consuming_batches = (
            self.env["agx.batch.input"]
            .search([("lot_id", "=", lot.id)])
            .mapped("batch_id")
        )
        shipment_lots = self.env["agx.shipment.lot.line"].search(
            [("lot_id", "=", lot.id), ("shipment_id.state", "!=", "cancelled")]
        )
        quants = self.env["stock.quant"].search(
            [("lot_id", "=", lot.id), ("location_id.usage", "=", "internal")]
        )
        on_hand = sum(quants.mapped("quantity"))

        parts = [
            "<h3>Lot: {}</h3>".format(self._fmt(lot.display_name))
        ]
        parts.append("<ul>")
        parts.append(
            "<li>Product: {}</li>".format(
                self._fmt(lot.product_id.display_name)
            )
        )
        parts.append(
            "<li>On Hand Qty: {}</li>".format(self._fmt(on_hand))
        )
        parts.append("</ul>")

        if batch_outputs:
            parts.append(
                "<h4>Produced In</h4>"
                "<table class='table table-sm table-bordered'>"
                "<thead><tr>"
                "<th>Batch</th><th>Product</th><th>Grade</th>"
                "<th>Size</th><th>Qty</th><th>Season</th>"
                "</tr></thead><tbody>"
            )
            for output in batch_outputs:
                parts.append(
                    "<tr><td>{}</td><td>{}</td><td>{}</td>"
                    "<td>{}</td><td>{}</td><td>{}</td></tr>".format(
                        self._fmt(output.batch_id.display_name),
                        self._fmt(output.product_id.display_name),
                        self._fmt(output.grade_id.display_name),
                        self._fmt(output.size_id.display_name),
                        self._fmt(output.qty),
                        self._fmt(
                            output.batch_id.season_id.name
                            if output.batch_id.season_id
                            else "-"
                        ),
                    )
                )
            parts.append("</tbody></table>")

        if consuming_batches:
            parts.append("<h4>Consumed In</h4><ul>")
            for batch in consuming_batches:
                parts.append(
                    "<li>{}</li>".format(self._fmt(batch.display_name))
                )
            parts.append("</ul>")

        if shipment_lots:
            parts.append(
                "<h4>Reserved In Shipments</h4>"
                "<table class='table table-sm table-bordered'>"
                "<thead><tr>"
                "<th>Shipment</th><th>Product</th><th>Reserved Qty</th>"
                "</tr></thead><tbody>"
            )
            for line in shipment_lots:
                parts.append(
                    "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                        self._fmt(line.shipment_id.display_name),
                        self._fmt(line.product_id.display_name),
                        self._fmt(line.reserved_qty),
                    )
                )
            parts.append("</tbody></table>")

        return "".join(parts)

    def action_generate(self):
        """Generate the traceability HTML report."""
        self.ensure_one()
        parts = []
        if self.shipment_id:
            parts.append(self._render_shipment_block(self.shipment_id))
        if self.batch_id:
            parts.append(self._render_batch_block(self.batch_id))
        if self.lot_id:
            parts.append(self._render_lot_block(self.lot_id))
        self.result_html = (
            "".join(parts) if parts else "<p>No data selected.</p>"
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "agx.traceability.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


# ---------------------------------------------------------------------------
# 4. Initialize Product Attributes Wizard
# ---------------------------------------------------------------------------
class AgxInitProductAttributesWizard(models.TransientModel):
    """One-time setup wizard for Grade/Size product variant bridge.

    This wizard creates (or finds) the 'Grade' and 'Size' product
    attributes in Odoo, then links every agx.grade and agx.size record
    to its matching ``product.attribute.value``.

    Optionally, when ``product_template_ids`` is set, the wizard also
    adds both attributes to those product templates — causing Odoo to
    generate all Grade × Size variants automatically.

    Idempotent: running it multiple times is safe.  Existing links are
    preserved; only missing ones are created.

    After running this wizard:
      - agx.grade.attribute_value_id is populated for all grades.
      - agx.size.attribute_value_id is populated for all sizes.
      - Selected product templates have Grade and Size attributes.
      - Stock reports show e.g. 'Orange / Grade A / Size 40' as a
        distinct product variant with its own inventory.
    """

    _name = "agx.init.product.attributes.wizard"
    _description = "Initialize Product Attributes"

    product_template_ids = fields.Many2many(
        "product.template",
        string="Finished-Goods Products",
        help=(
            "Select the product templates to add Grade and Size attributes to. "
            "Leave empty to only link agx.grade / agx.size records without "
            "modifying any product templates."
        ),
    )
    grade_attribute_name = fields.Char(
        default="Grade",
        required=True,
        help="Name of the Grade product attribute (created if not found).",
    )
    size_attribute_name = fields.Char(
        default="Size",
        required=True,
        help="Name of the Size product attribute (created if not found).",
    )
    summary = fields.Text(
        readonly=True,
        help="Result summary shown after the wizard runs.",
    )

    def _get_or_create_attribute(self, name):
        """Find or create a product attribute by name."""
        attr = self.env["product.attribute"].search(
            [("name", "=", name)], limit=1
        )
        if not attr:
            attr = self.env["product.attribute"].create(
                {
                    "name": name,
                    "create_variant": "always",
                }
            )
        return attr

    def _get_or_create_attribute_value(self, attribute, value_name):
        """Find or create an attribute value on a given attribute."""
        val = self.env["product.attribute.value"].search(
            [
                ("attribute_id", "=", attribute.id),
                ("name", "=", value_name),
            ],
            limit=1,
        )
        if not val:
            val = self.env["product.attribute.value"].create(
                {
                    "attribute_id": attribute.id,
                    "name": value_name,
                }
            )
        return val

    def _add_attribute_to_template(self, template, attribute, values):
        """Add an attribute + values to a product template if not present."""
        existing_line = template.attribute_line_ids.filtered(
            lambda l: l.attribute_id == attribute
        )
        if existing_line:
            # Add only missing values
            existing_values = existing_line.value_ids
            missing = values - existing_values
            if missing:
                existing_line.write(
                    {"value_ids": [(4, v.id) for v in missing]}
                )
        else:
            self.env["product.template.attribute.line"].create(
                {
                    "product_tmpl_id": template.id,
                    "attribute_id": attribute.id,
                    "value_ids": [(6, 0, values.ids)],
                }
            )

    def action_apply(self):
        """Run the attribute initialization.

        Steps:
        1. Get/create Grade attribute + one value per agx.grade.
        2. Link agx.grade.attribute_value_id.
        3. Get/create Size attribute + one value per agx.size.
        4. Link agx.size.attribute_value_id.
        5. (Optional) Add both attributes to selected product templates.
        6. Write summary.
        """
        self.ensure_one()
        lines = []

        # -- Grade --------------------------------------------------------
        grade_attr = self._get_or_create_attribute(self.grade_attribute_name)
        grades = self.env["agx.grade"].search([])
        grade_values = self.env["product.attribute.value"]
        for grade in grades:
            val = self._get_or_create_attribute_value(grade_attr, grade.name)
            if grade.attribute_value_id != val:
                grade.attribute_value_id = val
                lines.append(
                    "Grade '{}' linked to attribute value.".format(grade.name)
                )
            grade_values |= val
        lines.append(
            "Grade attribute '{}' — {} values configured.".format(
                grade_attr.name, len(grade_values)
            )
        )

        # -- Size ---------------------------------------------------------
        size_attr = self._get_or_create_attribute(self.size_attribute_name)
        sizes = self.env["agx.size"].search([])
        size_values = self.env["product.attribute.value"]
        for size in sizes:
            val = self._get_or_create_attribute_value(size_attr, size.name)
            if size.attribute_value_id != val:
                size.attribute_value_id = val
                lines.append(
                    "Size '{}' linked to attribute value.".format(size.name)
                )
            size_values |= val
        lines.append(
            "Size attribute '{}' — {} values configured.".format(
                size_attr.name, len(size_values)
            )
        )

        # -- Product templates --------------------------------------------
        for tmpl in self.product_template_ids:
            self._add_attribute_to_template(tmpl, grade_attr, grade_values)
            self._add_attribute_to_template(tmpl, size_attr, size_values)
            lines.append(
                "Product '{}' — Grade and Size attributes added. "
                "{} variants generated.".format(
                    tmpl.display_name,
                    len(tmpl.product_variant_ids),
                )
            )

        self.summary = "\n".join(lines) if lines else _("Nothing to do.")

        # Stay open to show summary
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

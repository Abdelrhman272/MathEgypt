# -*- coding: utf-8 -*-
import io
from odoo import http, _
from odoo.http import request


class ExportShipmentTraceabilityXlsx(http.Controller):

    @http.route("/export_shipment/traceability/xlsx/<int:wiz_id>", type="http", auth="user")
    def export_traceability_xlsx(self, wiz_id, **kwargs):
        Wizard = request.env["export.lot.traceability.wizard"]
        wiz = Wizard.browse(wiz_id).exists()

        if not wiz:
            return request.not_found()

        # Access check (Transient but still)
        wiz.check_access_rights("read")
        wiz.check_access_rule("read")

        move_lines = wiz._get_trace_move_lines()

        output = io.BytesIO()
        # xlsxwriter is available in Odoo environments عادة
        import xlsxwriter

        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        ws = workbook.add_worksheet("Traceability")

        # Formats
        fmt_header = workbook.add_format({"bold": True, "border": 1})
        fmt_cell = workbook.add_format({"border": 1})
        fmt_date = workbook.add_format({"border": 1, "num_format": "yyyy-mm-dd hh:mm"})

        headers = [
            "Date",
            "Lot/Serial",
            "Product",
            "Qty Done",
            "UoM",
            "Picking",
            "Picking Type",
            "Partner",
            "Source Location",
            "Destination Location",
            "Reference/Origin",
            "Company",
        ]

        for c, h in enumerate(headers):
            ws.write(0, c, h, fmt_header)

        row = 1
        for ml in move_lines:
            picking = ml.picking_id
            picking_type = wiz._format_picking_type(picking)
            partner = picking.partner_id.display_name if picking and picking.partner_id else ""
            origin = (picking.origin if picking else "") or (ml.move_id.origin if ml.move_id else "") or ""

            # Date (ml.date موجود)
            if ml.date:
                ws.write_datetime(row, 0, ml.date, fmt_date)
            else:
                ws.write(row, 0, "", fmt_cell)

            ws.write(row, 1, ml.lot_id.name or "", fmt_cell)
            ws.write(row, 2, ml.product_id.display_name or "", fmt_cell)
            ws.write_number(row, 3, float(ml.qty_done or 0.0), fmt_cell)
            ws.write(row, 4, ml.product_uom_id.name or "", fmt_cell)
            ws.write(row, 5, picking.name if picking else "", fmt_cell)
            ws.write(row, 6, picking_type, fmt_cell)
            ws.write(row, 7, partner, fmt_cell)
            ws.write(row, 8, ml.location_id.display_name or "", fmt_cell)
            ws.write(row, 9, ml.location_dest_id.display_name or "", fmt_cell)
            ws.write(row, 10, origin, fmt_cell)
            ws.write(row, 11, ml.company_id.display_name or "", fmt_cell)

            row += 1

        # Column widths
        ws.set_column(0, 0, 18)
        ws.set_column(1, 2, 22)
        ws.set_column(3, 4, 10)
        ws.set_column(5, 7, 18)
        ws.set_column(8, 11, 26)

        workbook.close()
        output.seek(0)

        filename = f"Traceability_{wiz.invoice_id.name or 'Invoice'}.xlsx"
        headers = [
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", http.content_disposition(filename)),
        ]
        return request.make_response(output.read(), headers=headers)

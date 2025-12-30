from odoo import models, fields, api
from collections import defaultdict

class LotTraceabilityWizard(models.TransientModel):
    _name = 'lot.traceability.wizard'
    _description = 'Lot Traceability Wizard'

    invoice_id = fields.Many2one('account.move', readonly=True)
    line_ids = fields.One2many('lot.traceability.line', 'wizard_id')

    moves_count = fields.Integer(compute='_compute_moves')

    def _compute_moves(self):
        for w in self:
            w.moves_count = len(w.line_ids)

    # ---------------------------------------------------------
    # ENTRY POINT
    # ---------------------------------------------------------
    def action_generate_traceability(self):
        self.ensure_one()
        self.line_ids.unlink()

        shipment = self._get_export_shipment()
        if not shipment or not shipment.reserved_picking_id:
            return

        delivery = shipment.reserved_picking_id

        finished_lots = delivery.move_line_ids.filtered(
            lambda ml: ml.qty_done > 0 and ml.lot_id
        ).mapped('lot_id')

        visited = set()
        for lot in finished_lots:
            self._trace_lot_recursive(
                lot=lot,
                level=0,
                visited=visited,
                delivery=delivery,
            )

    # ---------------------------------------------------------
    # CORE TRACE LOGIC (RECURSIVE)
    # ---------------------------------------------------------
    def _trace_lot_recursive(self, lot, level, visited, delivery=None):
        if lot.id in visited:
            return
        visited.add(lot.id)

        StockMoveLine = self.env['stock.move.line']

        # ---------- DELIVERY ----------
        if delivery:
            out_lines = delivery.move_line_ids.filtered(lambda ml: ml.lot_id == lot)
            for ml in out_lines:
                self._create_line(
                    level=level,
                    lot=lot,
                    ml=ml,
                    event_type='delivery',
                    reference=delivery.name,
                    partner=delivery.partner_id,
                )

        # ---------- PRODUCED BY MO ----------
        produced_lines = StockMoveLine.search([
            ('lot_id', '=', lot.id),
            ('state', '=', 'done'),
            ('move_id.production_id', '!=', False),
        ])

        for pl in produced_lines:
            mo = pl.move_id.production_id

            self._create_line(
                level=level + 1,
                lot=lot,
                ml=pl,
                event_type='production',
                reference=mo.name,
                production=mo,
            )

            # ---------- RAW CONSUMPTION ----------
            raw_lines = mo.move_raw_ids.move_line_ids.filtered(
                lambda ml: ml.state == 'done' and ml.lot_id
            )

            for raw in raw_lines:
                self._create_line(
                    level=level + 2,
                    lot=raw.lot_id,
                    ml=raw,
                    event_type='consumption',
                    reference=mo.name,
                )

                # recursive
                self._trace_lot_recursive(
                    lot=raw.lot_id,
                    level=level + 3,
                    visited=visited,
                )

        # ---------- VENDOR RECEIPT ----------
        incoming = StockMoveLine.search([
            ('lot_id', '=', lot.id),
            ('state', '=', 'done'),
            ('picking_id.picking_type_code', '=', 'incoming'),
        ], order='date asc', limit=1)

        if incoming:
            self._create_line(
                level=level + 10,
                lot=lot,
                ml=incoming,
                event_type='receipt',
                reference=incoming.picking_id.name,
                partner=incoming.picking_id.partner_id,
            )

    # ---------------------------------------------------------
    def _create_line(self, level, lot, ml, event_type,
                     reference=None, partner=None, production=None):

        self.env['lot.traceability.line'].create({
            'wizard_id': self.id,
            'level': level,
            'lot_id': lot.id,
            'product_id': ml.product_id.id,
            'qty': ml.qty_done,
            'uom_id': ml.product_uom_id.id,
            'event_type': event_type,
            'reference': reference,
            'partner_id': partner.id if partner else False,
            'picking_id': ml.picking_id.id if ml.picking_id else False,
            'production_id': production.id if production else False,
            'location_from': ml.location_id.id,
            'location_to': ml.location_dest_id.id,
            'date': ml.date,
        })

    # ---------------------------------------------------------
    def _get_export_shipment(self):
        self.ensure_one()
        return self.env['export.shipment'].search([
            ('sale_order_id', 'in', self.invoice_id.invoice_line_ids.mapped('sale_line_ids').mapped('order_id').ids)
        ], limit=1)

    # ---------------------------------------------------------
    # ACTIONS
    # ---------------------------------------------------------
    def action_open_screen(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Lot Traceability',
            'res_model': 'lot.traceability.line',
            'view_mode': 'tree',
            'domain': [('wizard_id', '=', self.id)],
        }

    def action_print_pdf(self):
        return self.env.ref('export_shipment.action_lot_traceability_report').report_action(self)

    def action_export_excel(self):
        return {
            'type': 'ir.actions.act_url',
            'url': f'/lot_traceability/excel/{self.id}',
            'target': 'self',
        }

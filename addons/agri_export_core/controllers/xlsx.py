
from odoo import http
from odoo.http import request

class TraceabilityXLSX(http.Controller):
    @http.route('/agri_export/traceability/xlsx', type='http', auth='user')
    def export(self, **kw):
        content = 'Traceability Export'
        return request.make_response(content, headers=[('Content-Type','text/plain')])

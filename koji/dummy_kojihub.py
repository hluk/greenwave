from xmlrpc.server import SimpleXMLRPCServer
from xmlrpc.server import SimpleXMLRPCRequestHandler


class RequestHandler(SimpleXMLRPCRequestHandler):
    rpc_paths = ('/RPC2',)


with SimpleXMLRPCServer(
        ('localhost', 8000), requestHandler=RequestHandler) as server:
    server.register_introspection_functions()

    @server.register_function
    def getTaskRequest(nvr):
        return ['_', 'fedora-99']

    @server.register_function
    def getBuild(nvr):
        name = nvr.split('-', 1)[0]
        return {'extra': {'source': {
            'original_url': f'http://localhost:5678/rpms/{name}.git#012abc'
        }}}

    server.serve_forever()

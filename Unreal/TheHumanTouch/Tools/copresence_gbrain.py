"""Read-only GeoTech context, using a dedicated host-provisioned OAuth client."""
import json
import os
from pathlib import Path

import httpx

ORIGIN = 'https://gbrain.codexremote.cc'

async def _read(operation, arguments):
    if operation not in {'search', 'get_page', 'whoami'}:
        raise ValueError('Unsupported GBrain read operation')
    path = os.environ.get('COPRESENCE_GBRAIN_CREDENTIAL_FILE')
    if not path:
        return {'available': False, 'reason': 'GBrain reader is not configured'}
    try:
        credential = json.loads(Path(path).read_text(encoding='utf-8'))
        async with httpx.AsyncClient(timeout=20, headers={'User-Agent':'GeoVerse-CoPresence/1.0'}) as client:
            response = await client.post(ORIGIN + '/token', data={
                'grant_type':'client_credentials', 'client_id':credential['clientId'],
                'client_secret':credential['clientSecret'], 'scope':'read',
                'resource':ORIGIN + '/mcp'})
            response.raise_for_status()
            token = response.json()['access_token']
        headers = {'Authorization':'Bearer ' + token, 'User-Agent':'GeoVerse-CoPresence/1.0',
                   'Accept':'application/json, text/event-stream'}
        async with httpx.AsyncClient(timeout=25, headers=headers) as client:
            def decode(response):
                response.raise_for_status()
                if 'text/event-stream' in response.headers.get('content-type', ''):
                    return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))
                return response.json()
            response = await client.post(ORIGIN + '/mcp', json={'jsonrpc':'2.0','id':1,
                'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},
                'clientInfo':{'name':'geoverse-reader','version':'1.0'}}})
            initialized = decode(response)
            if 'error' in initialized:
                return {'available':False,'reason':'GBrain initialization rejected'}
            if response.headers.get('mcp-session-id'):
                client.headers['mcp-session-id'] = response.headers['mcp-session-id']
            client.headers['MCP-Protocol-Version'] = initialized['result']['protocolVersion']
            notice = await client.post(ORIGIN + '/mcp', json={'jsonrpc':'2.0','method':'notifications/initialized'})
            notice.raise_for_status()
            result = decode(await client.post(ORIGIN + '/mcp', json={'jsonrpc':'2.0','id':2,
                'method':'tools/call','params':{'name':operation,'arguments':arguments}}))
            payload = result.get('result')
            if not isinstance(payload, dict):
                return {'available':False,'reason':'GBrain read rejected'}
            return {key:payload[key] for key in ('content','isError') if key in payload}
    except Exception as error:
        # Never expose HTTP request bodies, bearer tokens, or credential paths.
        return {'available': False, 'reason': 'GBrain read failed', 'error_type':type(error).__name__}

async def read(operation, query='', slug=''):
    if operation == 'search':
        if not query.strip() or len(query) > 1000:
            raise ValueError('Search requires a query of at most 1000 characters')
        args = {'query':query,'source_id':'default','limit':5,'snippet_chars':1600}
    elif operation == 'get_page':
        if not slug or len(slug) > 500:
            raise ValueError('Provide a page slug returned by search')
        args = {'slug':slug,'source_id':'default'}
    elif operation == 'whoami':
        args = {}
    else:
        raise ValueError('Unsupported GBrain read operation')
    return await _read(operation,args)

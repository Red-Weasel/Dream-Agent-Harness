"""Explicit, authenticated edits to private project documents and memory notes."""
import json
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route
from ..projects.documents import ProjectDocuments, _context
from ..projects.workspace import StaleRevision


def routes(server):
    async def endpoint(request):
        if not server._authorized(request):
            return JSONResponse({'error':'unauthorized'},status_code=401)
        origin=request.headers.get('origin')
        if request.method=='POST' and origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
            return JSONResponse({'error':'Origin does not match this session.'},status_code=403)
        try:
            from ..projects.library import ProjectLibrary
            project_id=request.path_params['project_id']
            # Saved project identity is authoritative; never accept a client path.
            await run_in_threadpool(ProjectLibrary().get,project_id)
            service=ProjectDocuments(project_id)
            identifier=request.path_params.get('document_id')
            if request.method=='POST':
                raw=bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw)>160_000:return JSONResponse({'error':'Document request exceeds 160 KB.'},status_code=413)
                payload=json.loads(raw)
                if not isinstance(payload,dict):raise ValueError('Expected a document object.')
                result=await run_in_threadpool(service.save,payload,identifier)
            elif identifier:
                result=await run_in_threadpool(service.get,identifier)
            else:
                documents=await run_in_threadpool(service.list)
                result={'documents':[{k:v for k,v in d.items() if k!='content'} for d in documents],
                        'context_chars':len(_context(documents)),
                        'max_context_chars':6000}
            return JSONResponse(result,headers={'Cache-Control':'no-store'})
        except StaleRevision as exc:
            return JSONResponse({'error':str(exc)},status_code=409)
        except (KeyError,FileNotFoundError):
            return JSONResponse({'error':'Project or document was not found.'},status_code=404)
        except RecursionError:
            return JSONResponse({'error':'Document structure is too deeply nested.'},status_code=400)
        except (ValueError,TypeError,OSError) as exc:
            return JSONResponse({'error':str(exc)},status_code=400)
    return [Route('/api/projects/{project_id}/documents',endpoint,methods=['GET','POST']),
            Route('/api/projects/{project_id}/documents/{document_id}',endpoint,methods=['GET','POST'])]

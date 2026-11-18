"""Read-only catalog presence check; never run inference or populate Fog cache."""
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response
import psycopg2


def create_router(connect):
    router = APIRouter()

    @router.get('/api/products/{barcode}/availability')
    def availability(barcode: Annotated[str, Path(pattern=r'^[0-9]{8,14}$')], response: Response):
        response.headers['Cache-Control'] = 'no-store'
        connection = None
        try:
            connection = connect()
            connection.set_session(readonly=True, autocommit=True)
            with connection.cursor() as cursor:
                cursor.execute('SELECT EXISTS(SELECT 1 FROM products WHERE barcode=%s)', (barcode,))
                exists = bool(cursor.fetchone()[0])
            return {'barcode': barcode, 'exists': exists, 'source': 'cloud_database'}
        except psycopg2.Error:
            raise HTTPException(503, '目前無法確認商品資料，請稍後再試。')
        finally:
            if connection is not None:
                connection.close()

    return router

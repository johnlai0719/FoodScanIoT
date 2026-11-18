from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import psycopg2
import pytest

from product_availability import create_router


@pytest.mark.parametrize('exists', [True, False])
def test_availability_uses_readonly_catalog_query(exists):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (exists,)
    app = FastAPI()
    app.include_router(create_router(lambda: connection))
    response = TestClient(app).get('/api/products/4710063338953/availability')
    assert response.json() == {'barcode': '4710063338953', 'exists': exists, 'source': 'cloud_database'}
    assert response.headers['cache-control'] == 'no-store'
    connection.set_session.assert_called_once_with(readonly=True, autocommit=True)
    cursor.execute.assert_called_once_with('SELECT EXISTS(SELECT 1 FROM products WHERE barcode=%s)', ('4710063338953',))
    connection.close.assert_called_once()


def test_database_outage_is_not_reported_as_missing():
    def unavailable():
        raise psycopg2.OperationalError('private connection detail')
    app = FastAPI()
    app.include_router(create_router(unavailable))
    response = TestClient(app).get('/api/products/4710063338953/availability')
    assert response.status_code == 503
    assert 'exists' not in response.json()
    assert 'private' not in response.text


def test_invalid_barcode_does_not_access_database():
    connect = MagicMock()
    app = FastAPI()
    app.include_router(create_router(connect))
    assert TestClient(app).get('/api/products/TEST/availability').status_code == 422
    connect.assert_not_called()

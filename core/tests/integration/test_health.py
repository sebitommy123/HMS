def test_health_reports_both_backends(client):
    r = client.get("/health")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["postgres"] == "reachable"
    assert body["trino"] == "reachable"
    assert body["status"] == "ok"

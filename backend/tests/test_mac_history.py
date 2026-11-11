"""El historial de consultas se guarda por MAC: si el dispositivo cambia de IP sigue siendo el mismo."""
import time

from bipon.db import init_db


def test_history_follows_mac(tmp_path):
    db = init_db(tmp_path / "t.db")
    now = time.time()
    mac = "aa:bb:cc:dd:ee:01"
    for i in range(5):
        db.log_query((now - 100 + i, "192.168.18.40", "a.example", "A", 0, "", "1.1.1.1", 1.0, mac))
    for i in range(3):  # el router le dio otra IP
        db.log_query((now - 10 + i, "192.168.18.77", "b.example", "A", 0, "", "1.1.1.1", 1.0, mac))
    db.log_query((now, "192.168.18.50", "c.example", "A", 0, "", "1.1.1.1", 1.0))  # fila antigua sin MAC (8 campos)
    db.flush_queries()
    rows = db.query("SELECT COALESCE(mac, client) who, COUNT(*) n FROM queries GROUP BY who ORDER BY n DESC")
    assert rows[0]["who"] == mac and rows[0]["n"] == 8
    n = db.scalar("SELECT COUNT(*) FROM queries WHERE (mac=? OR (mac IS NULL AND client=?))", (mac, "192.168.18.77"))
    assert n == 8

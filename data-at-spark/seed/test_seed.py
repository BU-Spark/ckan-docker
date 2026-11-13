"""Run: python test_seed.py  (no network, no pytest needed)."""
import copy

import seed

m = seed.load()
seed.validate(m)
assert len(m["datasets"]) == 28, len(m["datasets"])


def rejects(mutate):
    x = copy.deepcopy(m)
    mutate(x)
    try:
        seed.validate(x)
    except AssertionError:
        return True
    return False


assert rejects(lambda x: x["datasets"][0].__setitem__("topics", ["not-a-topic"]))
assert rejects(lambda x: x["datasets"][1].__setitem__("name", x["datasets"][0]["name"]))
ptr = next(d for d in m["datasets"] if d["kind"] == "pointer")
i = m["datasets"].index(ptr)
assert rejects(lambda x: x["datasets"][i]["resources"][0].__setitem__("format", "CSV"))
assert rejects(lambda x: x["datasets"][i]["resources"][0].__setitem__("url", "https://data.boston.gov/dataset/x/resource/y/download/a.csv"))
assert rejects(lambda x: x["datasets"][i]["resources"][0].__setitem__("url", "https://example.com/dataset/x"))
assert seed.check_url("http://localhost:5000/") == "http://localhost:5000"
try:
    seed.check_url("http://example.com")
except SystemExit:
    pass
else:
    raise AssertionError("plain http to remote host accepted")
print("test_seed OK")

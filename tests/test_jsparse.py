from mercswitch.jsparse import extract_assignment, extract_token


def test_extracts_nested_javascript_objects():
    source = 'var data={names:["WAN;guest", "x"], nested:{a:1}}; var g_tid = 12345;'
    assert extract_assignment(source, "data")["nested"]["a"] == 1
    assert extract_token(source) == "12345"


def test_extracts_new_array():
    assert extract_assignment("var ports = new Array(1, 2, 3);", "ports") == [1, 2, 3]

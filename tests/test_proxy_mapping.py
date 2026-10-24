import pytest

from circuitbreaker.models import ToolKind
from circuitbreaker.proxy import ToolMapper, ToolOverride

K = ToolKind


def kinds(mapped, advisory=False):
    return [(m.call.kind, m.call.target) for m in mapped if m.advisory == advisory]


def test_shell_command():
    mapped = ToolMapper().map("run_command", {"command": "ls -la"})
    assert kinds(mapped) == [(K.SHELL, "ls -la")]


def test_command_with_separate_args_is_joined_and_quoted():
    mapped = ToolMapper().map("run", {"command": "npm", "args": ["run", "x y"]})
    assert kinds(mapped) == [(K.SHELL, "npm run 'x y'")]


def test_argv_list_command():
    mapped = ToolMapper().map("exec", {"cmd": ["rm", "-rf", "/"]})
    assert kinds(mapped) == [(K.SHELL, "rm -rf /")]


def test_argument_keys_are_case_insensitive_and_all_are_checked():
    mapped = ToolMapper().map("run", {"command": "ls", "Command": "rm -rf /"})
    assert (K.SHELL, "rm -rf /") in kinds(mapped)
    assert (K.SHELL, "ls") in kinds(mapped)


@pytest.mark.parametrize(
    ("tool", "kind"),
    [
        ("read_file", K.FILE_READ),
        ("list_directory", K.FILE_READ),
        ("search_files", K.FILE_READ),
        ("write_file", K.FILE_WRITE),
        ("edit_file", K.FILE_WRITE),
        ("create_directory", K.FILE_WRITE),
        ("process_file", K.FILE_WRITE),  # unknown verbs are treated as writes
    ],
)
def test_path_kind_follows_tool_name(tool, kind):
    assert kinds(ToolMapper().map(tool, {"path": "a.txt"})) == [(kind, "a.txt")]


def test_paths_list_becomes_one_call_each():
    mapped = ToolMapper().map("read_multiple_files", {"paths": ["a", "b"]})
    assert kinds(mapped) == [(K.FILE_READ, "a"), (K.FILE_READ, "b")]


def test_copy_reads_source_and_writes_destination():
    mapped = ToolMapper().map("get_copy", {"source": "a", "destination": "b"})
    assert kinds(mapped) == [(K.FILE_READ, "a"), (K.FILE_WRITE, "b")]


def test_move_writes_both_ends():
    mapped = ToolMapper().map("move_file", {"source": "a", "destination": "b"})
    assert kinds(mapped) == [(K.FILE_WRITE, "a"), (K.FILE_WRITE, "b")]


def test_http_and_sql():
    http = ToolMapper().map("fetch", {"url": "https://x.io", "method": "post"})
    assert kinds(http) == [(K.HTTP, "https://x.io")]
    assert http[0].call.args["method"] == "POST"
    assert kinds(ToolMapper().map("db", {"query": "select 1"})) == [(K.SQL, "select 1")]


def test_recognised_calls_get_an_advisory_name_call():
    mapped = ToolMapper().map("delete_all", {"command": "ls"})
    assert kinds(mapped, advisory=True) == [(K.OTHER, "tool:delete_all")]


@pytest.mark.parametrize(
    "arguments",
    [{}, {"foo": "bar"}, [], "str", None, {"command": 5}, {"command": ["ls", 1]}, {"path": ""}],
)
def test_unclassifiable_input_falls_back_to_other(arguments):
    mapped = ToolMapper().map("mystery", arguments)
    assert any(m.call.kind is K.OTHER and not m.advisory for m in mapped)


def test_bad_shape_keeps_other_recognised_calls_too():
    mapped = ToolMapper().map("t", {"command": "ls", "path": 7})
    assert (K.SHELL, "ls") in kinds(mapped)
    assert any(m.call.kind is K.OTHER and not m.advisory for m in mapped)


def test_override_maps_custom_argument():
    mapper = ToolMapper({"deploy": ToolOverride(kind=K.SHELL, arg="input")})
    assert kinds(mapper.map("deploy", {"input": "make release"})) == [(K.SHELL, "make release")]


def test_other_target_is_stable_json():
    a = ToolMapper().map("t", {"b": 1, "a": 2})[0].call.target
    assert a == 'tool:t {"a": 2, "b": 1}'


def test_agent_id_is_propagated():
    assert ToolMapper().map("t", {"command": "ls"}, agent_id="cursor")[0].call.agent_id == "cursor"

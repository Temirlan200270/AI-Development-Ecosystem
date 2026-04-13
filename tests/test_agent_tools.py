from unittest.mock import MagicMock

from temir.tools.agent_tools import AgentTools


def make_tools():
    mock_sandbox = MagicMock()
    mock_sandbox.execute_command.return_value = {
        "success": True,
        "stdout": "",
        "stderr": "",
    }
    tools = AgentTools(sandbox_manager=mock_sandbox)
    return tools, mock_sandbox


def test_remove_path_calls_sandbox_correctly():
    tools, mock_sandbox = make_tools()
    path_to_remove = "src/temp.py"
    ok = tools.remove_path(path_to_remove)
    assert ok is True
    mock_sandbox.execute_command.assert_called_once()
    called_command = mock_sandbox.execute_command.call_args[0][0]
    assert "shutil" in called_command
    assert path_to_remove in called_command


def test_copy_path_calls_sandbox_correctly():
    tools, mock_sandbox = make_tools()
    ok = tools.copy_path("a/b", "c/d")
    assert ok is True
    mock_sandbox.execute_command.assert_called_once()
    called_command = mock_sandbox.execute_command.call_args[0][0]
    assert "copy2" in called_command or "copytree" in called_command
    assert '"a/b"' in called_command
    assert '"c/d"' in called_command


def test_append_file_calls_sandbox_correctly():
    tools, mock_sandbox = make_tools()
    ok = tools.append_file("notes.txt", "line")
    assert ok is True
    mock_sandbox.execute_command.assert_called_once()
    called_command = mock_sandbox.execute_command.call_args[0][0]
    assert "open" in called_command
    assert "write" in called_command
    assert "notes.txt" in called_command

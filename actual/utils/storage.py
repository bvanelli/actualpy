import pathlib
import tempfile
from typing import Union


def get_base_tmp_folder() -> pathlib.Path:
    """Returns the temporary folder that the library should use to store temporary files if the user does not provide
    a folder path."""
    base_tmp_dir = pathlib.Path(tempfile.gettempdir())
    tmp_dir = base_tmp_dir / "actualpy"
    tmp_dir.mkdir(exist_ok=True)
    return tmp_dir


def get_tmp_folder(file_id: Union[str, None]) -> pathlib.Path:
    """Returns a base folder to store the file based on the file id. Will create the folder if not existing."""
    if not file_id:
        folder = pathlib.Path(tempfile.mkdtemp())
    else:
        folder = get_base_tmp_folder() / str(file_id)
        folder.mkdir(exist_ok=True)
    return folder

import uuid
from typing import Tuple
import subprocess
import os
import numpy as np
import random
import logging
import time
from typing import Callable, Any, Optional

def get_tmp_file_name(content: str, extension: str = '') -> str:
    return uuid.uuid4().hex + extension


def get_tmp_file(content: str, extension: str = '') -> str:
    filename = uuid.uuid4().hex + extension
    with open(filename, 'w') as f:
        f.write(content)
    return filename


def get_tmp_path() -> str:
    filename = uuid.uuid4().hex
    return filename


def run_command(command: str, stdin: Optional[str] = None) -> Tuple[str, str]:
    output = subprocess.run(command.split(), capture_output=True, text=True, input=stdin)
    return output.stdout, output.stderr


def deterministic(seed: int):
    os.environ['PYTHONHASHSEED'] = str(seed)
    np.random.seed(seed)  # Numpy module.
    random.seed(seed)  # Python random module.


def timeit(func: Callable) -> Any:
    def wrapped(*args, **kwargs):
        func_name = func.__name__
        logging.info(f'Running {func_name}')
        t0 = time.time()
        res = func(*args, **kwargs)
        t1 = time.time()
        logging.info(f'Run {func_name} in {t1-t0}s')
        return res
    return wrapped

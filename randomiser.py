import random
import string

from helper_types import ConstraintError
from typing import Dict, List, Tuple, Set

class Randomiser:
    defaults = { "int": (0, 10),
                 "float": (0, 10),
                 "double": (0, 10),
                 "char": " " + string.ascii_letters,
                 "bool": [True, False],
                 }
    def __init__(self, seed=None):
        random.seed(seed)

    def random_int(self, min_val=None, max_val=None):
        defaults = Randomiser.defaults["int"]

        min_val = defaults[0] if min_val is None else min_val
        max_val = defaults[1] if max_val is None else max_val

        if min_val > max_val:
            raise ConstraintError(f"can not constrain in {min_val} <= x <= {max_val}")

        return random.randint(min_val, max_val)

    def random_float(self, min_val=None, max_val=None):
        defaults = Randomiser.defaults["float"]

        min_val = defaults[0] if min_val is None else min_val
        max_val = defaults[1] if max_val is None else max_val

        if min_val > max_val:
            raise ConstraintError(f"can not constrain in {min_val} <= x <= {max_val}")

        return random.random() * (max_val - min_val) + min_val

    def random_double(self, min_val=None, max_val=None):
        defaults = Randomiser.defaults["double"]

        min_val = defaults[0] if min_val is None else min_val
        max_val = defaults[1] if max_val is None else max_val

        if min_val > max_val:
            raise ConstraintError(f"can not constrain in {min_val} <= x <= {max_val}")

        return self.random_float(min_val=min_val, max_val=max_val)

    def random_char(self, alphabet=None):
        if alphabet is None:
            alphabet = Randomiser.defaults["char"]

        if len(alphabet) < 1:
            raise ConstraintError("can not select random char from empty set")

        return random.choice(alphabet)

    def random_bool(self):
        return random.choice(Randomiser.defaults["bool"])

    def random_array(self, length: int, elem_gen):
        assert length >= 0
        return [elem_gen() for _ in range(length)]

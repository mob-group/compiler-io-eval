import random
import string
from typing import Optional

class Randomiser:
    def __init__(self, seed=None):
        random.seed(seed)

    def random_int(self, min_val=0, max_val=10):
        return random.randint(min_val, max_val)

    def random_float(self, min_val=0, max_val=10):
        return random.random() * (max_val-min_val) + min_val

    def random_double(self, min_val=0, max_val=10):
        return self.random_float(min_val=min_val, max_val=max_val)

    def random_char(self, alphabet=" " + string.ascii_letters):
        return random.choice(alphabet)

    def random_bool(self):
        return random.choice([True, False])

    def random_array(self, length: int, elem_gen=None):
        assert length >= 0
        return [elem_gen() for _ in range(length)]

    def random_string(self, max_len, alphabet=None):
        length = self.random_int(max_val=max_len)

        if alphabet is None:
            gen = lambda: self.random_char()
        else:
            gen = lambda: self.random_char(alphabet=alphabet)

        return "".join(self.random_array(length, gen))
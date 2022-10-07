from helper_types import *
from reference_parser import FunctionReference
from runner import Function
import os
from dataclasses import dataclass, asdict
from typing import List

ReferenceFile = Tuple[FunctionReference, os.DirEntry]
ImplementationFile = Tuple[Function, os.DirEntry]


@dataclass
class Metrics:
    n_chars: int

    @classmethod
    def create_metrics(cls, ref: FunctionReference, implementation: ImplementationFile) -> 'Metrics':
        ref.code  # reference implementation code
        implementation[0].lib_path  # PATH to implementation .so
        implementation[0].original_code  # PATH to implementation code
        #print(implementation[0].original_code)
        with open(implementation[0].original_code, 'r') as f:
            n_chars = len(f.read())
        return Metrics(n_chars=n_chars)#raise NotImplementedError

    @classmethod
    def reduce(cls, metrics_list: List['Metrics']) -> Optional['Metrics']:
        if len(metrics_list) == 0:
            return None
        reduced = Metrics(n_chars=min([m.n_chars for m in metrics_list]))
        return reduced


    #def __str__(self):
    #    return str(asdict(self))

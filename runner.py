import ctypes
import os
import sys
from dataclasses import dataclass
from multiprocessing import Pool, Value, Queue, Process
from typing import *

import lumberjack
import randomiser
import reference_parser
import utilities
from helper_types import *
from reference_parser import FunctionReference, ParamSize, Constraint, GlobalContstraint, \
    ParamConstraint, CType


@dataclass
class Parameter:
    name: str
    type: CType
    is_output: bool
    constraints: list[ParamConstraint]

    size: Optional[ParamSize] = None
    ref: Optional = None

    def pack(self, value: SomeValue, length: Optional[int] = None):
        assert self.type != CType("void", 0)
        if self.type.contents == "char":
            value = value.encode("ascii")

        def scalar():
            return self.primitive()(value)

        def array():
            return (self.primitive() * length)(*value)

        def string():
            if self.is_output:
                return ctypes.create_string_buffer(value, length+1)
            else:
                sized = ctypes.c_char_p(b'0' * length)
                sized.value = value

                return sized

        if self.type.pointer_level < 0 or self.type.pointer_level > 1:
            raise UnsupportedTypeError("non-scalar/array")

        if self.type.pointer_level == 0:
            self.ref = scalar()
        elif self.type.pointer_level == 1:
            assert length is not None

            if self.type.contents == "char":
                self.ref = string()
            else:
                self.ref = array()

        return self.ref

    def unpack(self, foreign) -> AnyValue:
        if self.type.contents == "void":
            return None

        if not self.is_array():
            return foreign.value
        elif self.type.contents == "char":
            return foreign.value.decode()
        else:
            return foreign[:]

    def primitive(self):
        prim = self.type.contents

        if prim == "int":
            return ctypes.c_int
        elif prim == "float":
            return ctypes.c_float
        elif prim == "double":
            return ctypes.c_double
        elif prim == "char":
            return ctypes.c_char
        elif prim == "bool":
            return ctypes.c_bool
        else:
            raise UnsupportedTypeError(prim)

    def is_array(self) -> bool:
        return self.type.pointer_level == 1

    def get_size(self, value: Optional[ArrayValue], values: ParameterMapping) -> int:
        def with_val():
            size = self.size.evaluate(values, False)
            if isinstance(self.size, reference_parser.ConstSize):
                return len(value)
            else:
                assert size >= len(value)
                return size

        def without_val():
            size = self.size.evaluate(values, True)
            if isinstance(self.size, reference_parser.ConstSize) or isinstance(self.size, reference_parser.ExprSize):
                return randomiser.Randomiser().random_int(max_val=size)
            else:
                return size

        if value is None:
            return without_val()
        else:
            return with_val()

    @property
    def value(self):
        assert self.is_output and self.ref is not None
        return self.unpack(self.ref)


def get_parameters(parameters: list[reference_parser.CParameter],
                   param_info: reference_parser.FunctionInfo,
                   param_constraints: dict[str, list[Constraint]]):

    def fill_parameter(parameter: reference_parser.CParameter):
        return Parameter(parameter.name,
                         parameter.type,
                         param_info.is_output(parameter),
                         param_constraints.get(parameter.name, []),
                         size=param_info.size(parameter))

    return [fill_parameter(parameter) for parameter in parameters]


class Function:
    """
    An executable version of a C function
    """

    def __init__(self, reference: FunctionReference, lib_path: str):
        if not (lib_path.startswith("./") or lib_path.startswith("/")):
            lib_path = f"./{lib_path}"

        lib = ctypes.CDLL(lib_path)
        exe = getattr(lib, reference.name)

        self.name = reference.name
        self.lib_path = lib_path

        self.constraints, param_constraints = self.split_constraints(reference.info.constraints)

        self.parameters = get_parameters(reference.parameters, reference.info, param_constraints)

        self.type = reference.type

        # only works for scalar outputs
        if reference.type == CType("void", 0):
            exe.restype = None
        else:
            assert reference.type.pointer_level == 0
            return_val = Parameter(f"{self.name}_return", reference.type, True, [])
            exe.restype = return_val.primitive()

        self.exe = exe

    def run_safe(self, params: ParameterMapping):
        with Pool(processes=1) as pool:
            val = pool.apply(self.run, (params))
            print(val)


    def run(self, params: ParameterMapping):
        def setup_arg(parameter: Parameter):
            native_val = params[parameter.name]

            size = parameter.get_size(native_val, params) if parameter.is_array() else None
            foreign_val = parameter.pack(native_val, size)

            parameter.ref = foreign_val

            return foreign_val

        args = [setup_arg(param) for param in self.parameters]

        try:
            return self.exe(*args)
        except Exception as e:
            raise FunctionRunError(f"could not run function {self.name}")

    def outputs(self) -> ParameterMapping:
        """
        Get the values of the output parameters

        NOTE: this does not check the function has been run, so only use it after calling the function.

        :return: names and values of all output parameters
        """
        return {param.name: param.value for param in self.parameters if param.is_output}

    def safe_parameters(self) -> list[Parameter]:
        scalars = [param for param in self.parameters if not param.is_array()]
        arrays = [param for param in self.parameters if param.is_array()]

        return scalars + arrays

    @staticmethod
    def split_constraints(constraints: list[reference_parser.Constraint]) -> tuple[
        list[GlobalContstraint], dict[str, list[ParamConstraint]]]:
        param_constraints: dict[str, list[ParamConstraint]]
        param_constraints = {}

        global_constraints: list[GlobalContstraint]
        global_constraints = []
        for constraint in constraints:
            if isinstance(constraint, reference_parser.GlobalContstraint):
                global_constraints.append(constraint)
            elif isinstance(constraint, reference_parser.ParamConstraint):
                param = param_constraints.get(constraint.var, [])
                param.append(constraint)

                param_constraints[constraint.var] = param

        return global_constraints, param_constraints

    def satisfied(self, inputs: ParameterMapping) -> bool:
        if not (self.constraints or any(parameter.constraints for parameter in self.parameters)):
            return True

        globals = (eval(constraint.predicate, dict(inputs)) for constraint in self.constraints)
        parameter = (eval(f"{constraint.var} {constraint.op} {constraint.val}", dict(inputs)) for parameter in self.parameters for constraint in parameter.constraints)

        return all(globals) and all(parameter)


def compile_lib(path_to_compilable: str, lib_path: str):
    """
    Compile a reference to a usable version

    This usable version is a shared object, or a dynamic library.
    This version is designed for Linux, for macOS change the -soname to -install_name.
    Not sure how to support Windows, but there will probably be much bigger changes somewhere else too.

    :param path_to_compilable: a function to compile, can be .c or .s
    :param lib_path: the .so file to compile into
    """
    linker_flag = "soname" if sys.platform == "linux" else "install_name"
    cmd = f"gcc -Wall -O0 -shared -fPIC -o {lib_path} {path_to_compilable}"
    stdout, stderr = utilities.run_command(cmd)
        

    if stderr:
        lumberjack.getLogger("error").error(stderr)
        raise CompilationError(path_to_compilable, lib_path)


def create(path_to_reference: str, path_to_compilable: str = None, lib_path: str = None) -> Function:
    """
    Helper to generate an executable

    :param path_to_reference: the reference directory
    :param path_to_compilable: the path to the version of the reference to compile,
    if :code:`None` then use "ref.c" in the reference directory
    :param lib_path: the .so file to compile into, generates random if not given
    :return:
    """
    if path_to_compilable is None:
        ref_file = "ref.c"
        path_to_compilable = os.path.join(path_to_reference, ref_file)

    ref = reference_parser.load_reference(path_to_reference)

    return create_from(ref, path_to_compilable, lib_path)


def create_from(reference: FunctionReference, path_to_compilable: str, lib_path: str = None) -> Function:
    if lib_path is None:
        if not os.path.exists(tmp_dir):
            os.makedirs(tmp_dir)
        lib_path = os.path.join("_tmp", f"{utilities.get_tmp_path()}.so")

    compile_lib(path_to_compilable, lib_path)

    return Function(reference, lib_path)

def create_and_run(reference: FunctionReference, path_to_lib: str, inputs: ParameterMapping, queue: Optional[Queue] = None):
    func = Function(reference, path_to_lib)

    val = func.run(inputs)
    outputs = func.outputs()

    queue.put((val, outputs))

def run_safe(reference: FunctionReference, path_to_lib: str, inputs: ParameterMapping) -> Optional[tuple[AnyValue, ParameterMapping]]:
    q = Queue()

    p = Process(target=create_and_run, args=(reference, path_to_lib, inputs, q))
    p.start()
    p.join()

    if p.exitcode == 0:
        return q.get_nowait()
    else:
        lumberjack.getLogger("error").warning(f"{path_to_lib} failed on an input")
        return None

'''
if __name__ == '__main__':
    run = create("synthesis-eval/examples/str_len", lib_path="test.so")

    for param in run.parameters:
        print(param)

    #vs = {"arr": list(range(10)), "n": 10}
    vs = {"str": "klajflkjsdlkjfklajsdlkfjalksdfjklasjefd"}

    print(run.run_safe(vs))
    for output in run.outputs():
        print(output)
'''

if __name__ == '__main__':
    ref_name = "synthesis-eval/examples/str_cat"
    ref = reference_parser.load_reference(ref_name)
    inputs = {"src": "aaaaa", "out": "bbbbb"}

    #run = create_from(ref, f"{ref_name}/ref.c", "test.so")
    run = create_from(ref, "str_cat.c", "str_cat.so")

    for _ in range(10):
        resp = run_safe(ref, run.lib_path, inputs)
        if resp is None:
            print("nothing came back")
            continue

        val, outputs = resp
        print(val)
        print("========")
        for name, output in outputs.items():
            print(f"{name}: {output}")

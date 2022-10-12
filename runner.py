import ctypes
import os
import sys
from dataclasses import dataclass
from multiprocessing import Queue, Process
from typing import Dict, List, Tuple, Set

import lumberjack
import randomiser
import reference_parser
import utilities
from helper_types import *
from reference_parser import FunctionReference, ParamSize, Constraint, GlobalContstraint, \
    ParamConstraint, CType


@dataclass
class Parameter:
    """
    A C function parameter

    Brings together all information about the parameter, which is spread out across a FunctionReference
    """
    name: Name
    type: CType
    is_output: bool
    constraints: List[ParamConstraint]

    size: Optional[ParamSize] = None
    ref: Optional = None

    def pack(self, value: SomeValue, length: Optional[int] = None):
        """
        Create a foreign value that can be passed as this parameter

        Uses the parameter information to determine how to convert it.

        :param value: the native value to convert
        :param length: if present this is used to determine the size of a foreign array,
        otherwise the length of the value is used (only for array parameters).
        :return: the foreign value that has been created
        """
        assert self.type != CType("void", 0)
        if self.type.contents == "char":
            value = value.encode("ascii")

        def scalar():
            return self.primitive()(value)

        def array():
            return (self.primitive() * length)(*value)

        def string():
            if self.is_output:
                return ctypes.create_string_buffer(value, length + 1)
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
        """
        Converts a foreign value into a native value

        Uses the parameter information to determine how to convert it.

        :param foreign: the foreign value
        :return: the native value
        """
        if self.type.contents == "void":
            return None

        if not self.is_array():
            return foreign.value
        elif self.type.contents == "char":
            return foreign.value.decode()
        else:
            return foreign[:]

    def primitive(self):
        """
        Maps type descriptions to their corresponding ctypes representations

        :return: the ctypes class for the current parameters primitive type
        """
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
        """
        Retrieve the size for the current (array) parameter

        Can be used to determine the size of the initial (native value) array,
        or the final (foreign) array.

        :param value: passing a list here means the initial array has been generated,
        so the size of the final array is returned.
        :param values: mapping of currently generated parameter values
        :return: the size of the array
        """

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
        """
        Gives the native value that this parameter currently holds

        Parameters contain a reference to a foreign object, stored whenever the parameter packs a new value.
        As this reference points to the underlying object it can be used to check a value after the function has run,
        if the function modified the value at all in the process this is shown in the reference.
        This function is just a convenient way to check the value of an output value as a native object.

        :return: the value of the reference stored in the parameter
        """
        assert self.is_output and self.ref is not None
        return self.unpack(self.ref)

    @staticmethod
    def get(parameters: List[reference_parser.CParameter],
            param_info: reference_parser.FunctionInfo,
            param_constraints: Dict[str, List[Constraint]]):
        """
        Builds a list of parameters

        :param parameters: the parameters as parsed from the reference
        :param param_info: the extra info parsed from the reference
        :param param_constraints: the constraints for each parameter, indexed by the parameters name
        :return: the parameters which tie all this information together
        """

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

    def __init__(self, reference: FunctionReference, lib_path: str, original_code: Optional[str] = None):
        if not (lib_path.startswith("./") or lib_path.startswith("/")):
            lib_path = f"./{lib_path}"

        lib = ctypes.CDLL(lib_path)
        exe = getattr(lib, reference.name)

        self.name = reference.name
        self.lib_path = lib_path

        self.constraints, param_constraints = self.split_constraints(reference.info.constraints)

        self.parameters = Parameter.get(reference.parameters, reference.info, param_constraints)

        self.type = reference.type

        # only works for scalar outputs
        if reference.type == CType("void", 0):
            exe.restype = None
        else:
            assert reference.type.pointer_level == 0
            return_val = Parameter(Name(f"{self.name}_return"), reference.type, True, [])
            exe.restype = return_val.primitive()

        self.exe = exe

        self.original_code = original_code

    def run(self, params: ParameterMapping):
        """
        Run the function on a set of inputs

        Throws a FunctionRunError if these inputs do not work.

        :param params: a dict containing (parameter name, value for this function call)
        :return: the (native) value of running the function on those inputs
        """

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

    def safe_parameters(self) -> List[Parameter]:
        """
        Get an ordering of parameters with no backwards dependencies

        Since some parameters are dependent on the value of other parameters this function retrieves the parameters
        in an order such that a parameter can be generated using only the values of parameters that appear before it.

        Currently the only dependencies captured are sizes of an array when the size is the value of another parameter.
        As the size of an array must be scalar,
        a safe ordering is assured if scalar parameters are determined before arrays.

        Note this may not always work as some dependencies cannot be determined,
        for example a simple expression size of an array may refer to another parameter
        but this dependency is not captured anywhere.

        :return: the safe ordering
        """
        scalars = [param for param in self.parameters if not param.is_array()]
        arrays = [param for param in self.parameters if param.is_array()]

        return scalars + arrays

    @staticmethod
    def split_constraints(constraints: List[reference_parser.Constraint]) -> Tuple[
        List[GlobalContstraint], Dict[Name, List[ParamConstraint]]]:
        """
        Split a full list of constraints into global and parameter constraints

        :param constraints: the full list
        :return: the global constraints, and the parameter constraints mapped to the parameter they are for
        """
        param_constraints: Dict[Name, List[ParamConstraint]]
        param_constraints = {}

        global_constraints: List[GlobalContstraint]
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
        """
        Checks that all constraints (global or parameter) are satisfied by a given set of inputs

        :param inputs: the values of the input parameters
        :return: :code:`True` if all constraints are satisfied
        """
        if not (self.constraints or any(parameter.constraints for parameter in self.parameters)):
            return True

        globals = (constraint.satisfied(inputs) for constraint in self.constraints)
        parameter = (constraint.satisfied(inputs) for parameter in self.parameters for constraint in
                     parameter.constraints)

        return all(globals) and all(parameter)


def compile_lib(path_to_compilable: str, lib_path: str, optLevel: str = '0'):
    """
    Compile a reference to a usable version

    This usable version is a shared object, or a dynamic library.
    This version is designed for Linux, for macOS change the -soname to -install_name.
    Not sure how to support Windows, but there will probably be much bigger changes somewhere else too.

    :param path_to_compilable: a function to compile, can be .c or .s
    :param lib_path: the .so file to compile into
    """
    linker_flag = "soname" if sys.platform == "linux" else "install_name"
    cmd = f"gcc -Wall -O{optLevel} -shared -fPIC -o {lib_path} {path_to_compilable}"
    stdout, stderr = utilities.run_command(cmd)

    if stderr:
        lumberjack.getLogger("error").error(stderr)
        raise CompilationError(path_to_compilable, lib_path)


def create(path_to_reference: str, path_to_compilable: str = None, lib_path: str = None) -> Function:
    """
    Helper to generate an executable directly from files, compiling into a library

    :param path_to_reference: the reference directory
    :param path_to_compilable: the path to the version of the reference to compile,
    if :code:`None` then use "ref.c" in the reference directory
    :param lib_path: the .so file to compile into, generates random if not given
    :return: the executable function
    """
    if path_to_compilable is None:
        ref_file = "ref.c"
        path_to_compilable = os.path.join(path_to_reference, ref_file)

    ref = reference_parser.load_reference(path_to_reference)

    return create_from(ref, path_to_compilable, lib_path)


def create_from(reference: FunctionReference, path_to_compilable: str, lib_path: str = None) -> Function:
    """
    Generate an executable, compiling into a library

    :param reference: a reference to model the executable on
    :param path_to_compilable: the path to the implementation to compile
    :param lib_path: the .so file to compile into, generates random if not given
    :return: the executable function
    """
    if lib_path is None:
        if not os.path.exists(tmp_dir):
            os.makedirs(tmp_dir)
        lib_path = os.path.join("_tmp", f"{utilities.get_tmp_path()}.so")

    compile_lib(path_to_compilable, lib_path)

    return Function(reference, lib_path, path_to_compilable)


def create_and_run(reference: FunctionReference, path_to_lib: str, inputs: ParameterMapping,
                   queue: Queue, original_impl_path: Optional[str] = None):
    """
    Builds a function, runs it on a set of inputs, and enqueues its result for later use

    Necessary for running a function trial in its own process.
    Unfortunately a :code:`Function` object, or more specifically the ctypes references it contains (?),
    do not work nicely with multiprocessing.
    This means that a Function can not be passed into another process,
    the workaround for this is to build the Function inside the new process.

    The queue is used so that the values obtained by running the function can be accessed in the parent process.

    :param reference: the function reference to build the function from
    #:param path_to_lib: the path of the (already compiled) library to use
    :param inputs: the inputs to run the function on
    :param queue: the queue the results (return value and output parameter values) should be stored on

    :param original_impl_path: needed for code metrics
    """
    func = Function(reference, path_to_lib, original_impl_path)

    val = func.run(inputs)
    outputs = func.outputs()

    queue.put((val, outputs))


def run_safe(reference: FunctionReference, path_to_lib: str, inputs: ParameterMapping,
             path_to_orig_impl: Optional[str] = None) -> Optional[
    Tuple[AnyValue, ParameterMapping]]:
    """
    Runs a function on a set of inputs, sand-boxed in a separate process

    :param reference: the reference of the function to run
    :param path_to_lib: the path of the (already compiled) library to use
    :param inputs: the inputs to run the function on
    :path_to_orig_impl needed for code metrics
    :return: the results (return value and output parameter values) obtained from running the function
    """
    q = Queue()

    # p = Process(target=create_and_run, args=(reference, path_to_lib, inputs, q))
    p = Process(target=create_and_run, args=(reference, path_to_lib, inputs, q, path_to_orig_impl))
    p.start()
    timeout = 2  # num. of seconds to wait
    p.join(timeout)

    if p.exitcode == 0:
        p.close()
        return q.get_nowait()
    else:
        p.close()
        path_to_lib = path_to_lib
        lumberjack.getLogger("error").warning(f"{path_to_lib} failed on an input")
        return None

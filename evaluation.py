from dataclasses import dataclass
from textwrap import indent, dedent
from typing import Any
from typing import Dict, List, Tuple, Set

import math

import lumberjack
from examples import ExampleInstance, parse, form
from helper_types import *
from randomiser import Randomiser
from reference_parser import FunctionReference
from runner import Function, Parameter, run_safe


class Generator:
    """
    Generates examples

    To generate examples this class produces a random input,
    runs it on a "correct" version of the function,
    and then retrieves the output produced by that code.
    """

    def __init__(self, reference: FunctionReference, runner: Function):
        self.reference = reference
        self.runner = runner

        random_seed = None  # non-random seed doesn't play nicely with processes
        self.randomiser = Randomiser(seed=random_seed)

    def generate(self, n: int) -> List[ExampleInstance]:
        """
        Used to generate multiple examples

        :param n: the number of examples to make
        :return: the examples generated
        """
        assert n > 0

        try:
            examples = []
            fails = 0
            max_fails = n

            while fails < max_fails and len(examples) < n:
                example = self.generate_single()

                if example is not None:
                    examples.append(example)
                    fails -= 1
                else:
                    fails += 1

            return examples
        except FunctionRunError:
            lumberjack.getLogger("error").error(f"issue calling function {self.runner.name}")
            return []

    def generate_input(self) -> ParameterMapping:
        """
        Used to generate the input for one example

        :return: the values for input parameters
        """
        inputs = {}

        for param in self.runner.safe_parameters():
            inputs[param.name] = self.random(param, inputs)

        return inputs

    def generate_single(self) -> Optional[ExampleInstance]:
        """
        Used to generate one example

        NOTE: this spawns a new process so that if the function crashes on these inputs the rest of the program is safe

        :return: the example generated
        """
        inputs = self.generate_input()

        if not self.runner.satisfied(inputs):
            return None

        results = run_safe(self.reference, self.runner.lib_path, inputs)
        if results is None:
            raise FunctionRunError(f"could not produce value from {self.reference.name}")

        value, outputs = results
        return ExampleInstance(inputs, value, outputs)

    def random(self, parameter: Parameter, current: ParameterMapping) -> SomeValue:
        """
        Generate a random value for an input parameter

        As some parameters depend on others, this function needs to know the values of parameters already generated.
        Hopefully this will be called in an order where dependencies are generated before their dependents.

        This checks the constraints on a parameter too to limit the random sample space to valid values.

        :param parameter: the parameter to generate a value for
        :param current: the values already generated
        :return: the new parameter value
        """

        max_val: Optional[SomeValue] = None
        min_val: Optional[SomeValue] = None

        for constraint in parameter.constraints:
            if constraint.op in {"<", "<="}:
                if max_val is None or max_val < constraint.value:
                    max_val = constraint.value
            elif constraint.op in {">", ">="}:
                if min_val is None or min_val > constraint.value:
                    min_val = constraint.value

        primitive = parameter.type.contents
        if primitive == "int":
            def gen():
                return self.randomiser.random_int(min_val=min_val, max_val=max_val)
        elif primitive == "float":
            def gen():
                return self.randomiser.random_float(min_val=min_val, max_val=max_val)
        elif primitive == "double":
            def gen():
                return self.randomiser.random_double(min_val=min_val, max_val=max_val)
        elif primitive == "char":
            def gen():
                return self.randomiser.random_char()
        elif primitive == "bool":
            def gen():
                return self.randomiser.random_bool()
        else:
            raise UnsupportedTypeError(primitive)

        if not parameter.is_array():
            val = gen()
        else:
            size = parameter.get_size(None, current)

            val = self.randomiser.random_array(size, gen)
            val = val if primitive != "char" else ''.join(val)

        return val


@dataclass
class Failure:
    expected: ExampleInstance
    value: Any
    outputs: Dict[str, Any]

    def __str__(self):
        return f"input {self.expected.inputs} produced incorrect values (expected vs. real);\
         {self.expected.value} vs. {self.value}; {self.expected.outputs} vs. {self.outputs}"


class Result:
    """
    Contains the results of testing a series of inputs on a function
    """

    def __init__(self, passes: int, tests: int, failures: List[Failure], name: str = None):
        assert passes >= 0 and tests >= 0  # len(failures) is implicitly >= 0
        assert passes + len(failures) == tests

        self.passes = passes
        self.tests = tests
        self.failures = failures
        self.name = name

    def passed(self) -> bool:
        """
        :return: :code:`True` if all tests passed
        """
        return self.passes == self.tests

    def is_trivial(self) -> bool:
        """
        :return: :code:`True` if no tests were run
        """
        return self.tests == 0

    def full(self, show_fails: bool) -> str:
        """
        Formats the result into a nice description

        :param show_fails: set to :code:`True` to put any failed tests in the description
        :return: the prettified result
        """
        if show_fails and self.failures:
            return dedent('''\
            {summ}

            {failures}\
            ''').format(summ=self, failures=self.show_failures())
        else:
            return str(self)

    def show_failures(self) -> str:
        """
        :return: any failures, formatted nicely
        """
        return indent("\n".join(str(failure) for failure in self.failures), " >> ")

    def __str__(self):
        status = "OK" if self.passed else "NOT OK"
        name = "" if self.name is None else f"{self.name}: "
        return f"{name}passed {self.passes}/{self.tests} tests ({status})"


class Evaluator:
    """
    Evaluates functions on examples
    """

    def __init__(self, reference: FunctionReference, runner: Function):
        self.reference = reference
        self.runner = runner

    @staticmethod
    def read(example_file: str) -> List[ExampleInstance]:
        """
        Parses an example file into examples to use

        :param example_file: the file containing examples
        :return: the examples in the file
        """
        with open(example_file, "r") as f:
            sig = f.readline()
            examples = f.readlines()

        return parse(sig, examples)

    def transform(self, examples: List[ExampleInstance]):
        """
        Currently useless, put all cleanup code for examples here

        TODO: some smarter stuff

        For example could pair up misnamed examples,
        infer size variables for arrays,
        etc.

        :param examples: the examples to fix
        :return: the fixed examples
        """
        return examples

    def check_example(self, example: ExampleInstance) -> Optional[Failure]:
        """
        Runs an example and checks whether the result matches the expected

        NOTE: this spawns a new process so that if the function crashes on these inputs the rest of the program is safe

        :param example: the example to use
        :return: whether or not the output of the example matches the expected output
        """

        def check_value(expected_value: AnyValue, actual_value: AnyValue) -> bool:
            if expected_value == actual_value:
                return True

            try:
                return math.isnan(expected_value) == math.isnan(actual_value)
            except TypeError:
                return False

        result = run_safe(self.reference, self.runner.lib_path, example.inputs)

        if result is None:
            raise FunctionRunError(f"no value produced by {self.reference.name}")

        value, actual = result
        expected = example.outputs

        fail = Failure(example, value, actual)

        if not check_value(example.value, value):
            return fail

        for param in expected:
            if not check_value(expected[param], actual[param]):
                return fail

        return None

    def check(self, examples: List[ExampleInstance]) -> Result:
        """
        Evaluates many examples, uses :code:`check_example`

        :param examples: the examples to evaluate
        :return: a tuple of the form (number of successes, number of trials, [details of failures])
        """
        examples = self.transform(examples)
        passes = 0
        failures = []

        for example in examples:
            failure = self.check_example(example)
            if failure is None:
                passes += 1
            else:
                failures.append(failure)

        return Result(passes, len(examples), failures)


def evaluate(ref: FunctionReference, run: Function, ex_file: str) -> Result:
    """
    Helper method to check a reference function against examples

    :param ref: the reference of the function
    :param run: the executable of the function
    :param ex_file: the examples to evaluate with
    :return: the evaluation result of the test
    """
    e = Evaluator(ref, run)
    examples = e.read(ex_file)
    result = e.check(examples)

    lumberjack.getLogger("failure").error(result.show_failures())

    return result


def generate(ref: FunctionReference, run: Function, n: int) -> List[ExampleInstance]:
    """
    Helper method to produce examples to check against a function

    :param ref: the reference of the function
    :param run: the executable of the function
    :param n: the number of examples to make
    :return: the examples produced
    """
    g = Generator(ref, run)
    examples = g.generate(n)

    return examples


def write_examples(ref: FunctionReference, examples: List[ExampleInstance], ex_file: str):
    """
    Writes a collection of examples to a file

    :param ref: the reference the examples are for
    :param examples: the examples
    :param ex_file: the file to write the examples into
    """
    with open(ex_file, "w") as f:
        f.write('\n'.join(form(ref, examples)))

from textwrap import indent, dedent
from typing import Optional, Any

import math

import lumberjack
import utilities
from examples import ExampleInstance, parse, form
from randomiser import Randomiser
from reference_parser import load_reference, FunctionReference
from runner import Function, create, create_from, FunctionRunError, Parameter
from helper_types import *


class Generator:
    """
    Generates examples

    To generate examples this class produces a random input,
    runs it on a "correct" version of the function,
    and then retrieves the output produced by that code.
    """

    def __init__(self, runner: Function):
        self.runner = runner

        random_seed = 0  # change to `None' outside of testing
        self.randomiser = Randomiser(seed=random_seed)

    def generate(self, n: int) -> list[ExampleInstance]:
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

    def generate_input(self) -> dict[str, SomeValue]:
        inputs = {}

        for param in self.runner.safe_parameters():
            inputs[param.name] = self.random(param, inputs)

        return inputs

    def generate_single(self) -> Optional[ExampleInstance]:
        """
        Used to generate one example

        :return: the example generated
        """
        inputs = self.generate_input()

        if not self.runner.satisfied(inputs):
            return None

        value = self.runner.run(inputs)

        outputs = self.runner.outputs()

        return ExampleInstance(inputs, value, outputs)

    def random(self, parameter: Parameter, current: ParameterMapping) -> SomeValue:
        """
        Generate a random value for an input parameter

        As some parameters depend on others, this function needs to know the values of parameters already generated.
        Hopefully this will be called in an order where dependencies are generated before their dependents.

        :param parameter: the parameter to generate a value for
        :param current: the values already generated
        :return: the new parameter value
        """

        primitive = parameter.type.contents
        # TODO: use paramter constraints to select from valid range
        # add in any range changes here
        # NOTE: that's why they're all funcs
        if primitive == "int":
            def gen():
                return self.randomiser.random_int()
        elif primitive == "float":
            def gen():
                return self.randomiser.random_float()
        elif primitive == "double":
            def gen():
                return self.randomiser.random_double()
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
            return val if primitive != "char" else ''.join(val)

        current[parameter.name] = val
        return val


class Failure:
    def __init__(self, expected: ExampleInstance, value: Any, outputs: dict[str, Any]):
        self.expected = expected
        self.value = value
        self.outputs = outputs

    def __str__(self):
        return f"input {self.expected.inputs} produced incorrect values (expected vs. real);\
         {self.expected.value} vs. {self.value}; {self.expected.outputs} vs. {self.outputs}"


class Result:
    def __init__(self, passes: int, tests: int, failures: list[Failure], name: str = None):
        assert passes >= 0 and tests >= 0  # len(failures) is implicitly >= 0
        assert passes + len(failures) == tests

        self.passes = passes
        self.tests = tests
        self.failures = failures
        self.name = name

    def passed(self) -> bool:
        return self.passes == self.tests

    def is_trivial(self) -> bool:
        return self.tests == 0

    def full(self, show_fails: bool) -> str:
        if show_fails and self.failures:
            return dedent('''\
            {summ}

            {failures}\
            ''').format(summ=self, failures=self.show_failures())
        else:
            return str(self)

    def show_failures(self) -> str:
        return indent("\n".join(str(failure) for failure in self.failures), " >> ")

    def __str__(self):
        status = "OK" if self.passed else "NOT OK"
        name = "" if self.name is None else f"{self.name}: "
        return f"{name}passed {self.passes}/{self.tests} tests ({status})"


class Evaluator:
    """
    Evaluates functions on examples
    """

    def __init__(self, runner: Function):
        self.runner = runner

    @staticmethod
    def read(example_file: str) -> list[ExampleInstance]:
        """
        Parses an example file into examples to use

        :param example_file: the file containing examples
        :return: the examples in the file
        """
        with open(example_file, "r") as f:
            sig = f.readline()
            examples = f.readlines()

        return parse(sig, examples)

    def transform(self, examples: list[ExampleInstance]):
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

        value = self.runner.run(example.inputs)

        expected = example.outputs
        actual = self.runner.outputs()

        fail = Failure(example, value, actual)

        if not check_value(example.value, value):
            return fail

        for param in expected:
            if not check_value(expected[param], actual[param]):
                return fail

        return None

    def check(self, examples: list[ExampleInstance]) -> Result:
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


def evaluate(run: Function, ex_file: str) -> Result:
    """
    Helper method to check a reference function against examples

    :param ref: the reference of the function
    :param run: the executable of the function
    :param ex_file: the examples to evaluate with
    :return: the evaluation result of the test
    """
    e = Evaluator(run)
    examples = e.read(ex_file)
    result = e.check(examples)

    lumberjack.getLogger("failure").error(result.show_failures())

    return result


def generate(run: Function, n: int) -> list[ExampleInstance]:
    """
    Helper method to produce examples to check against a function

    :param ref: the reference of the function
    :param run: the executable of the function
    :param n: the number of examples to make
    :param ex_file: the file to save the examples into, if :code:`None` then don't write anywhere
    :return: the examples produced
    """
    g = Generator(run)
    examples = g.generate(n)

    return examples

def write_examples(ref: FunctionReference, examples: list[ExampleInstance], ex_file: str):
    with open(ex_file, "w") as f:
        f.write('\n'.join(form(ref, examples)))

if __name__ == '__main__':
    ref_dir = "synthesis-eval/examples/str_cat"
    ref = load_reference(ref_dir)
    run = create_from(ref, f"{ref_dir}/ref.c", lib_path="test.so")
    egs = generate(run, 200)
    write_examples(ref, egs, "test.examples")
    print(evaluate(run, "test.examples").full(True))


'''
if __name__ == '__main__':
    import argparse

    argparser = argparse.ArgumentParser()
    argparser.add_argument("ref", help="path to the reference program")

    subparsers = argparser.add_subparsers()

    eval_parser = subparsers.add_parser("eval")
    eval_parser.add_argument("-e", "--examples", help="path to file containing examples")
    eval_parser.add_argument("program", help="path to the program to evaluate")

    gen_parser = subparsers.add_parser("gen")
    gen_parser.add_argument("num_examples", type=int, help="number of examples to generate")
    gen_parser.add_argument("examples", help="path to examples file to write")

    args = argparser.parse_args()

    ref = load_reference(args.ref)
    try:
        # assumes its an eval
        run = create(args.ref, args.program)

        if args.examples is None:
            default_examples = 50

            example_file = utilities.get_tmp_path()
            # generate examples using the gcc compiled reference function
            # NOT the sample implementation
            example_run = create(args.ref)
            examples = generate(example_run, default_examples)
            write_examples(ref, examples, example_file)
        else:
            example_file = args.examples

        print(evaluate(run, example_file).full(True))
    except AttributeError:
        # it's a gen instead
        ref = load_reference(args.ref)
        run = create_from(ref, )
        assert args.num_examples > 0
        examples = generate(run, args.num_examples)
        write_examples()
'''
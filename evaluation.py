import sys

import utilities
from examples import ExampleInstance, ParameterMapping, parse, form
from randomiser import Randomiser
from reference_parser import FunctionReference, CParameter, load_reference, UnsupportedTypeError
from runner import Function, SomeValue, create, FunctionRunError


class UnsatisfiedDependencyError(Exception):
    pass


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
            return [self.generate_single() for _ in range(n)]
        except FunctionRunError as e:
            print(e.args[0], file=sys.stdout)
            return []

    def generate_single(self) -> ExampleInstance:
        """
        Used to generate one example

        :return: the example generated
        """
        inputs = {}
        for param in self.reference.parameters(safe_order=True):
            self.random(param, inputs)

        value = self.runner.run(**inputs)

        outputs = self.runner.outputs()

        return ExampleInstance(inputs, value, outputs)

    def random(self, parameter: CParameter, current: ParameterMapping) -> SomeValue:
        """
        Generate a random value for an input parameter

        As some parameters depend on others, this function needs to know the values of parameters already generated.
        Hopefully this will be called in an order where dependencies are generated before their dependents.

        :param parameter: the parameter to generate a value for
        :param current: the values already generated
        :return: the new parameter value
        """
        assert 0 <= parameter.type.pointer_level < 2

        scalar_type = parameter.type.contents
        # add in any range changes here
        # NOTE: that's why they're all lambdas
        if scalar_type == "int":
            gen = lambda: self.randomiser.random_int()
        elif scalar_type == "float":
            gen = lambda: self.randomiser.random_float()
        elif scalar_type == "double":
            gen = lambda: self.randomiser.random_double()
        elif scalar_type == "char":
            gen = lambda: self.randomiser.random_char()
        elif scalar_type == "bool":
            gen = lambda: self.randomiser.random_bool()
        else:
            raise UnsupportedTypeError(scalar_type)

        if parameter.type.pointer_level == 0:
            val = gen()
        elif parameter.type.contents == "char":
            max_str_len = 100
            val = self.randomiser.random_string(max_str_len)
        else:
            size = self.reference.info.size(parameter)

            if size not in current:
                raise UnsatisfiedDependencyError(f"could not find size ({size}) for parameter ({parameter.name})")
            else:
                size = current[size]

            val = self.randomiser.random_array(size, gen)

        current[parameter.name] = val
        return val

    def write(self, examples: list[ExampleInstance], file_name: str):
        """
        Writes the given examples to a file

        :param examples: examples to write
        :param file_name: file to write into
        """
        with open(file_name, "w") as f:
            output = form(self.reference, examples)
            f.writelines(f"{line}\n" for line in output)


Result = tuple[int, int, list[str]]


class Evaluator:
    """
    Evaluates functions on examples
    """
    def __init__(self, reference: FunctionReference, runner: Function):
        self.reference = reference
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

    def check_example(self, example: ExampleInstance) -> bool:
        """
        Runs an example and checks whether the result matches the expected

        :param example: the example to use
        :return: whether or not the output of the example matches the expected output
        """
        val = self.runner.run(**example.inputs)

        if val != example.value:
            return False

        expected = example.outputs
        actual = self.runner.outputs()

        for param in expected:
            if expected[param] != actual[param]:
                return False

        return True

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
            res = self.check_example(example)
            if res:
                passes += 1
            else:
                failures.append(str(example))

        return passes, len(examples), failures


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
    return e.check(examples)


def generate(ref: FunctionReference, run: Function, n: int, ex_file: str = None) -> list[ExampleInstance]:
    """
    Helper method to produce examples to check against a function

    :param ref: the reference of the function
    :param run: the executable of the function
    :param n: the number of examples to make
    :param ex_file: the file to save the examples into, if :code:`None` then don't write anywhere
    :return: the examples produced
    """
    g = Generator(ref, run)
    examples = g.generate(n)

    if ex_file is not None:
        g.write(examples, ex_file)

    return examples


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
            generate(ref, example_run, default_examples, example_file)
        else:
            example_file = args.examples

        passes, tests, failures = evaluate(ref, run, example_file)

        assert passes == tests or failures

        print(f"passed: {passes}/{tests}")
        if failures:
            print("failures:")
            for fail in failures:
                print(fail)
    except AttributeError:
        # it's a gen instead
        run = create(args.ref)
        assert args.num_examples > 0
        generate(ref, run, args.num_examples, args.examples)

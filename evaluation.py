import os.path
import runner
from reference_parser import FunctionReference, CParameter, load_reference
from randomiser import Randomiser
from runner import Function
from examples import ExampleInstance, ParameterMapping, parse, generate


class Generator:
    def __init__(self, reference: FunctionReference, runner: Function):
        self.reference = reference
        self.runner = runner

        random_seed = 0  # change to `None' outside of testing
        self.randomiser = Randomiser(seed=random_seed)

    def generate(self, n: int) -> list[ExampleInstance]:
        assert n > 0

        return [self.generate_single() for _ in range(n)]

    def generate_single(self) -> ExampleInstance:
        inputs = {}
        for param in self.reference.parameters(safe_order=True):
            self.random(param, inputs)

        value = self.runner.run(**inputs)

        outputs = self.runner.outputs()

        return ExampleInstance(inputs, value, outputs)

    def random(self, parameter: CParameter, current: ParameterMapping):
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
            raise Exception(f"can't produce value of type {scalar_type}")

        if parameter.type.pointer_level == 0:
            val = gen()
        elif parameter.type.contents == "char":
            max_str_len = 100
            val = self.randomiser.random_string(max_str_len)
        else:
            size = self.reference.info.size(parameter)

            if size not in current:
                raise Exception(f"could not find size ({size}) for parameter ({parameter.name})")
            else:
                size = current[size]

            val = self.randomiser.random_array(size, gen)

        current[parameter.name] = val
        return val

    def write(self, examples: list[ExampleInstance], file_name: str):
        with open(file_name, "w") as f:
            output = generate(self.reference, examples)
            f.writelines(f"{line}\n" for line in output)


class Evaluator:
    def __init__(self, reference: FunctionReference, runner: Function):
        self.reference = reference
        self.runner = runner

    @staticmethod
    def read(example_file: str) -> list[ExampleInstance]:
        with open(example_file, "r") as f:
            sig = f.readline()
            examples = f.readlines()

        return parse(sig, examples)

    def transform(self, examples: list[ExampleInstance]):
        return examples

    def check_example(self, example: ExampleInstance) -> bool:
        val = self.runner.run(**example.inputs)

        if val != example.value:
            return False

        expected = example.outputs
        actual = self.runner.outputs()

        for param in expected:
            if expected[param] != actual[param]:
                return False

        return True

    def check(self, examples: list[ExampleInstance]) -> tuple[int, int, list[str]]:
        examples = self.transform(examples)
        passes = 0
        failures = []

        for example in examples:
            res = self.check_example(example)
            if res:
                passes += 1
            else:
                failures.append(str(example))

        return (passes, len(examples), failures)


if __name__ == '__main__':
    path = "/Users/sami/Documents/haxx/internship/synthesis-eval/examples/"
    prog = "vfill"

    ref = load_reference(os.path.join(path, prog))
    exe = runner.Function(ref, "mega_ref.so")

    g = Generator(ref, exe)
    examples = g.generate(50)
    examples_file = f"{prog}_examples"
    g.write(examples, examples_file)

    e = Evaluator(ref, exe)
    print(e.check(examples))

    examples2 = Evaluator.read(examples_file)
    print(e.check(examples2))

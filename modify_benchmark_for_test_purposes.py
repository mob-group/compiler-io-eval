import os
import shutil

SYNTHESIS_EVAL = '/home/jordiae/PycharmProjects/io2vec/synthesis-eval/examples'
NEW = os.path.join(os.path.dirname(__file__), 'examples_c')


def main():
    for dir_ in sorted(os.listdir(SYNTHESIS_EVAL)):
        if not os.path.isdir(os.path.join(SYNTHESIS_EVAL, dir_)):
            continue
        os.makedirs(os.path.join(NEW, dir_), exist_ok=True)
        shutil.copy(os.path.join(SYNTHESIS_EVAL, dir_, 'ref.c'), os.path.join(NEW, dir_, f'{dir_}-1.c'))


if __name__ == '__main__':
    main()

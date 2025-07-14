from setuptools import setup

# The setup is currently only used for tests. See the README for installation
# instructions in Neovim.
setup(
    name='semshi',
    description='Semantic Highlighting for Python in Neovim',
    version='0.4.0',
    packages=['semshi'],
    # Original repo: https://github.com/numirias/semshi
    # author='numirias',
    # author_email='numirias@users.noreply.github.com',
    author='wookayin',
    author_email='wookayin@gmail.com',
    url='https://github.com/wookayin/semshi',
    license='MIT',
    python_requires='>=3.7',
    install_requires=[
        'pytest>=7.0',
        'pytest-pudb',
        'pynvim>=0.4.3',
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Topic :: Text Editors',
    ],
)

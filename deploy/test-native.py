"""Native verifier regression: stable method IDs and genuine final overrides."""
import os
from pathlib import Path
import subprocess
import tempfile

java = Path(os.getenv('BB_JAVA', '/opt/java6/bin/java')).parent
api = Path(os.getenv('BB_TOOLS_ROOT', '/tools')) / 'JDE71/lib/net_rim_api.jar'
verifier = os.getenv('BB_TEST_PREVERIFY', '/opt/bin/preverify')


def run(command, folder):
    return subprocess.run(command, cwd=folder, capture_output=True, text=True, timeout=30)


with tempfile.TemporaryDirectory(prefix='bb-verifier-test-') as temp:
    folder = Path(temp)
    (folder / 'Parent.java').write_text('public class Parent { public final int getContentHeight() { return 0; } }')
    (folder / 'Child.java').write_text('public class Child extends Parent { public int getHorizontalScroll() { return 0; } }')
    compile_command = [str(java / 'javac'), '-source', '1.3', '-target', '1.3', '-bootclasspath', str(api)]
    result = run(compile_command + ['Parent.java', 'Child.java'], folder)
    assert result.returncode == 0, result.stderr
    for order in [('Parent', 'Child'), ('Child', 'Parent')]:
        output = folder / ('out-' + order[0])
        output.mkdir()
        result = run([verifier, '-verbose', '-classpath', str(api) + ':' + str(folder), '-d', str(output), *order], folder)
        assert result.returncode == 0, result.stderr
        assert (output / 'Parent.class').exists() and (output / 'Child.class').exists()
    print('Legal inheritance passes in both class orders')

    # javac correctly refuses illegal overrides; compile against a non-final
    # parent, then replace only the parent bytecode with its final version.
    (folder / 'Parent.java').write_text('public class Parent { public int getContentHeight() { return 0; } }')
    (folder / 'Child.java').write_text('public class Child extends Parent { public int getContentHeight() { return 1; } }')
    result = run(compile_command + ['Parent.java', 'Child.java'], folder)
    assert result.returncode == 0, result.stderr
    (folder / 'Parent.java').write_text('public class Parent { public final int getContentHeight() { return 0; } }')
    result = run(compile_command + ['Parent.java'], folder)
    assert result.returncode == 0, result.stderr
    for order in [('Parent', 'Child'), ('Child', 'Parent')]:
        output = folder / ('bad-' + order[0])
        output.mkdir()
        result = run([verifier, '-verbose', '-classpath', str(api) + ':' + str(folder), '-d', str(output), *order], folder)
        assert result.returncode != 0 and 'overrides final method' in result.stderr, result.stderr
    print('Actual final method overrides are rejected in both class orders')

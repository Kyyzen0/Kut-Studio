#include <cstdlib>
#include <iostream>

int test_queue();
int test_decoder();
int test_engine();

int main() {
    int failed = 0;
    failed += test_queue();
    failed += test_decoder();
    failed += test_engine();
    if (failed == 0) {
        std::cout << "ALL TESTS PASSED\n";
        return 0;
    }
    std::cerr << failed << " test group(s) failed\n";
    return 1;
}

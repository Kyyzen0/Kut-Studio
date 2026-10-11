#include "kut_engine/frame_queue.hpp"

#include <atomic>
#include <chrono>
#include <iostream>
#include <thread>

using namespace kut;

static int expect(bool cond, const char* msg) {
    if (!cond) {
        std::cerr << "FAIL queue: " << msg << "\n";
        return 1;
    }
    return 0;
}

int test_queue() {
    int fails = 0;
    FrameQueue q(2);
    auto a = make_frame(16, 16, PixelFormat::RGB24);
    auto b = make_frame(16, 16, PixelFormat::RGB24);
    auto c = make_frame(16, 16, PixelFormat::RGB24);
    a->sequence = 1;
    b->sequence = 2;
    c->sequence = 3;

    fails += expect(q.push(a, true, std::chrono::milliseconds(0)), "push a");
    fails += expect(q.push(b, true, std::chrono::milliseconds(0)), "push b");
    fails += expect(q.push(c, true, std::chrono::milliseconds(0)), "push c drops oldest");
    fails += expect(q.dropped() == 1, "one drop");
    fails += expect(q.size() == 2, "size 2");

    auto p1 = q.pop(std::chrono::milliseconds(10));
    fails += expect(p1.has_value() && (*p1)->sequence == 2, "pop got seq 2");
    auto p2 = q.pop(std::chrono::milliseconds(10));
    fails += expect(p2.has_value() && (*p2)->sequence == 3, "pop got seq 3");

    q.cancel();
    fails += expect(!q.push(a, false, std::chrono::milliseconds(1)), "push after cancel");
    fails += expect(!q.pop(std::chrono::milliseconds(1)).has_value(), "pop after cancel empty");

    // Saturation without drop: producer waits, then cancel unblocks.
    FrameQueue q2(1);
    auto f = make_frame(8, 8, PixelFormat::RGB24);
    fails += expect(q2.push(f, false, std::chrono::milliseconds(0)), "fill q2");
    std::atomic<bool> done{false};
    std::thread t([&] {
        auto g = make_frame(8, 8, PixelFormat::RGB24);
        q2.push(g, false, std::chrono::milliseconds(500));
        done = true;
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    q2.cancel();
    t.join();
    fails += expect(done.load(), "blocked push woke on cancel");

    if (fails == 0) {
        std::cout << "PASS test_queue\n";
    }
    return fails ? 1 : 0;
}

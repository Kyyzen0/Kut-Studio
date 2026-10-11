cmake_minimum_required(VERSION 3.20)
project(kut_native_video_engine VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

option(KUT_ENGINE_BUILD_PYTHON "Build pybind11 Python module" ON)
option(KUT_ENGINE_BUILD_TESTS "Build C++ tests" ON)
option(KUT_ENGINE_ASAN "Enable AddressSanitizer" OFF)

find_package(PkgConfig REQUIRED)
pkg_check_modules(FFMPEG REQUIRED IMPORTED_TARGET
    libavcodec libavformat libavutil libswscale)

set(KUT_ENGINE_SOURCES
    src/frame.cpp
    src/frame_queue.cpp
    src/metrics.cpp
    src/decoder.cpp
    src/renderer.cpp
    src/engine.cpp
)

if(APPLE)
    enable_language(OBJCXX)
    list(APPEND KUT_ENGINE_SOURCES src/renderer_metal.mm)
    add_compile_definitions(KUT_ENGINE_HAS_METAL=1)
    find_library(METAL_FRAMEWORK Metal REQUIRED)
    find_library(FOUNDATION_FRAMEWORK Foundation REQUIRED)
else()
    list(APPEND KUT_ENGINE_SOURCES src/renderer_metal_stub.cpp)
endif()

add_library(kut_engine STATIC ${KUT_ENGINE_SOURCES})
set_target_properties(kut_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(kut_engine PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(kut_engine PUBLIC PkgConfig::FFMPEG)
if(APPLE)
    target_link_libraries(kut_engine PUBLIC ${METAL_FRAMEWORK} ${FOUNDATION_FRAMEWORK})
endif()

if(KUT_ENGINE_ASAN)
    target_compile_options(kut_engine PUBLIC -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(kut_engine PUBLIC -fsanitize=address,undefined)
endif()

add_executable(kut_engine_cli apps/kut_engine_cli.cpp)
target_link_libraries(kut_engine_cli PRIVATE kut_engine)

if(KUT_ENGINE_BUILD_TESTS)
    enable_testing()
    add_executable(kut_engine_tests
        tests/test_main.cpp
        tests/test_queue.cpp
        tests/test_decoder.cpp
        tests/test_engine.cpp
    )
    target_link_libraries(kut_engine_tests PRIVATE kut_engine)
    add_test(NAME kut_engine_tests COMMAND kut_engine_tests)
endif()

if(KUT_ENGINE_BUILD_PYTHON)
    find_package(Python3 COMPONENTS Interpreter Development REQUIRED)
    # Prefer system/user pybind11; fall back to FetchContent.
    find_package(pybind11 CONFIG QUIET)
    if(NOT pybind11_FOUND)
        include(FetchContent)
        FetchContent_Declare(
            pybind11
            GIT_REPOSITORY https://github.com/pybind/pybind11.git
            GIT_TAG v2.13.6
        )
        FetchContent_MakeAvailable(pybind11)
    endif()
    pybind11_add_module(kut_engine_py bindings/pybind_module.cpp)
    target_link_libraries(kut_engine_py PRIVATE kut_engine)
    set_target_properties(kut_engine_py PROPERTIES OUTPUT_NAME "kut_engine_native")
endif()

message(STATUS "kut_engine: FFmpeg ok; Python=${KUT_ENGINE_BUILD_PYTHON}; Tests=${KUT_ENGINE_BUILD_TESTS}")

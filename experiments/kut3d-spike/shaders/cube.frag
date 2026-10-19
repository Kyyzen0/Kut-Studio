#version 440
layout(location = 0) in vec2 vUv;
layout(location = 1) in float vShade;
layout(location = 0) out vec4 fragColor;
layout(binding = 1) uniform sampler2D uTex;
void main() {
    vec4 c = texture(uTex, vUv);
    fragColor = vec4(c.rgb * vShade, 1.0);
}

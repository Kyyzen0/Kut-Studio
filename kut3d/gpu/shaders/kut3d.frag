#version 440
// Kut-3D, moteur GPU : PBR métal / rugosité (mêmes formules que kut3d/render/shading.py), espace linéaire.
// Sorties : 0 = couleur linéaire PRÉMULTIPLIÉE + alpha (RGBA 32 bits flottants), 1 = profondeur de vue (R32F).
layout(location = 0) in vec3 vWorld;
layout(location = 1) in vec3 vNrm;
layout(location = 2) in vec2 vUv;
layout(location = 3) in vec4 vCol;
layout(location = 4) in vec3 vTan;
layout(location = 5) in float vSign;
layout(location = 6) in float vDepth;

layout(location = 0) out vec4 outColor;
layout(location = 1) out vec4 outDepth;

layout(std140, binding = 0) uniform FrameBlock {
    mat4 viewProj;
    vec4 eye;
    vec4 ambient;
    vec4 counts;
    vec4 lightA[16];
    vec4 lightB[16];
};

layout(std140, binding = 1) uniform DrawBlock {
    mat4 model;
    mat4 nmat;
    vec4 base;
    vec4 emissive;
    vec4 params;
    vec4 flags0;
    vec4 flags1;
};

layout(binding = 3) uniform sampler2D baseTex;       // sRGB (décodé en linéaire par le matériel avant filtrage)
layout(binding = 4) uniform sampler2D normalTex;
layout(binding = 5) uniform sampler2D mrTex;         // G = rugosité, B = métal
layout(binding = 6) uniform sampler2D emissiveTex;   // sRGB
layout(binding = 7) uniform sampler2D occlusionTex;  // R

const float PI = 3.14159265358979;
const float MIN_ROUGHNESS = 0.045;  // kut3d/render/materials.py

vec3 unit(vec3 v) {
    float l = length(v);
    return l > 1e-20 ? v / l : vec3(0.0);
}

void main() {
    vec4 baseColor = vCol * base;
    if (flags0.x > 0.5) {
        baseColor *= texture(baseTex, vUv);
    }
    float mode = params.w;
    if (mode > 0.5 && mode < 1.5 && baseColor.a < params.z) {
        discard;  // alpha_mode mask
    }

    vec3 view = unit(eye.xyz - vWorld);
    vec3 n = unit(vNrm);
    if (length(n) < 0.5) {
        // normale absente ou dégénérée : normale géométrique, orientée vers l'observateur
        n = unit(cross(dFdx(vWorld), dFdy(vWorld)));
        if (dot(n, view) < 0.0) {
            n = -n;
        }
    } else if (!gl_FrontFacing) {
        n = -n;
    }
    if (flags0.y > 0.5) {
        vec3 ts = unit(texture(normalTex, vUv).xyz * 2.0 - 1.0);
        vec3 t = unit(vTan - n * dot(n, vTan));
        float sgn = vSign < 0.0 ? -1.0 : 1.0;
        vec3 b = cross(n, t) * sgn;
        vec3 o = unit(t * ts.x + b * ts.y + n * ts.z);
        if (length(o) > 0.5) {
            n = o;
        }
    }

    float metallic = params.x;
    float rough = params.y;
    if (flags0.z > 0.5) {
        vec4 mr = texture(mrTex, vUv);
        rough *= mr.g;
        metallic *= mr.b;
    }
    rough = clamp(rough, MIN_ROUGHNESS, 1.0);
    vec3 em = emissive.rgb;
    if (flags0.w > 0.5) {
        em *= texture(emissiveTex, vUv).rgb;
    }
    float occlusion = 1.0;
    if (flags1.x > 0.5) {
        occlusion = texture(occlusionTex, vUv).r;
    }

    vec3 albedo = baseColor.rgb;
    vec3 f0 = vec3(0.04) * (1.0 - metallic) + albedo * metallic;
    vec3 diffuse = albedo * (1.0 - metallic);
    float a = rough * rough;
    float a2 = a * a;
    float nv = clamp(dot(n, view), 1e-4, 1.0);
    vec3 outc = ambient.rgb * albedo * occlusion;
    int count = int(counts.x + 0.5);
    for (int i = 0; i < count; i++) {
        vec3 toward;
        vec3 radiance = lightB[i].rgb;
        if (lightA[i].w < 0.5) {
            toward = lightA[i].xyz;
        } else {
            vec3 vec = lightA[i].xyz - vWorld;
            float d2 = max(dot(vec, vec), 1e-8);
            float atten = 1.0 / d2;
            float range = lightB[i].w;
            if (range > 0.0) {
                float ratio = d2 / (range * range);
                float win = clamp(1.0 - ratio * ratio, 0.0, 1.0);
                atten *= win * win;
            }
            toward = vec / sqrt(d2);
            radiance *= atten;
        }
        float nl = clamp(dot(n, toward), 0.0, 1.0);
        vec3 h = unit(toward + view);
        float nh = clamp(dot(n, h), 0.0, 1.0);
        float vh = clamp(dot(view, h), 0.0, 1.0);
        float om = 1.0 - vh;
        float om2 = om * om;
        vec3 fres = f0 + (vec3(1.0) - f0) * (om2 * om2 * om);
        float denom = nh * nh * (a2 - 1.0) + 1.0;
        float dist = a2 / (PI * denom * denom);
        float vis = 0.5 / max(nl * sqrt(nv * nv * (1.0 - a2) + a2)
                              + nv * sqrt(nl * nl * (1.0 - a2) + a2), 1e-8);
        vec3 spec = fres * dist * vis;
        outc += (spec + (vec3(1.0) - fres) * diffuse / PI) * nl * PI * radiance;
    }

    vec3 rgb = clamp(outc + em, 0.0, 1.0);
    if (mode > 1.5) {
        float al = clamp(baseColor.a, 0.0, 1.0);
        outColor = vec4(rgb * al, al);  // prémultiplié : le mélange du matériel est (1, 1 - alpha)
    } else {
        outColor = vec4(rgb, 1.0);
    }
    outDepth = vec4(vDepth, 0.0, 0.0, 0.0);
}

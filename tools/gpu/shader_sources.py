"""Sources GLSL (Vulkan 4.40) des passes de l'aperçu GPU.

Une seule source par passe, compilée par ``qsb`` (Qt Shader Baker) vers SPIR-V,
GLSL, HLSL et MSL : le même shader sert Metal, Direct3D, Vulkan et OpenGL.
Les ``.qsb`` produits sont versionnés dans ``assets/shaders`` : l'application
n'a pas besoin de ``qsb`` pour tourner. Régénérer après modification ::

    python -m tools.gpu.build_shaders

Le bloc ``Params`` est **le même** pour toutes les passes (seulement des
``vec4`` / ``mat4`` : aucune surprise d'alignement std140) ; son contenu est
écrit par :class:`core.gpu_composite.Uniforms`. Les opérations ponctuelles
(:data:`OPS`) suivent :func:`core.gpu_effects._apply_point` à l'identique.
"""

from __future__ import annotations

HEADER = """#version 440
layout(std140, binding = 0) uniform Params {
    mat4 yuv_to_rgb;
    mat4 rgb_to_yuv;
    mat4 inverse_map;
    vec4 target;   // largeur, hauteur, retournement Y, taps
    vec4 source;   // largeur, hauteur, échelle des valeurs, disposition
    vec4 fit;      // rectangle utile (uv)
    vec4 pad;      // couleur des bandes / du fond
    vec4 state;    // espace d'entrée, espace de sortie, nb d'opérations, BGRA
    vec4 comp;     // opacité, mode de fusion, matte, bord adouci
    vec4 blur;     // direction x, y, rayon luma, rayon chroma
    vec4 misc;     // netteté
    vec4 reserved;
    vec4 weights_luma[25];
    vec4 weights_chroma[25];
    vec4 ops[32];
};
"""

VERTEX = HEADER + """
layout(location = 0) in vec4 vertex;
layout(location = 0) out vec2 v_uv;
void main() {
    v_uv = vertex.zw;
    gl_Position = vec4(vertex.x, vertex.y * target.z, 0.0, 1.0);
}
"""

FRAGMENT_HEADER = HEADER + """
layout(location = 0) in vec2 v_uv;
layout(location = 0) out vec4 fragColor;
layout(binding = 1) uniform sampler2D tex0;
layout(binding = 2) uniform sampler2D tex1;
layout(binding = 3) uniform sampler2D tex2;

vec3 to_space(vec3 c, int from_space, int to_space_) {
    if (from_space == to_space_) {
        return c;
    }
    if (to_space_ == 1) {
        return clamp((yuv_to_rgb * vec4(c, 1.0)).rgb, 0.0, 1.0);
    }
    return clamp((rgb_to_yuv * vec4(c, 1.0)).rgb, 0.0, 1.0);
}
"""

OPS = """
vec3 apply_ops(vec3 c, inout int space, vec2 px, vec2 size) {
    int count = int(state.z + 0.5);
    for (int i = 0; i < 8; ++i) {
        if (i >= count) {
            break;
        }
        vec4 h = ops[i * 4];
        int kind = int(h.x + 0.5);
        int wanted = kind == 4 ? 1 : 0;
        c = to_space(c, space, wanted);
        space = wanted;
        if (kind == 1) {
            vec4 h2 = ops[i * 4 + 1];
            if (h2.y > 0.5) {
                float y8 = floor(c.x * 255.0 + 0.5);
                c.x = clamp(floor(y8 * h.y) + h.z, 0.0, 255.0) / 255.0;
            }
            if (h2.z > 0.5) {
                vec2 c8 = floor(c.yz * 255.0 + 0.5);
                c.yz = clamp(floor(c8 * h.w) + h2.x, 0.0, 255.0) / 255.0;
            }
        } else if (kind == 2) {
            vec2 half_size = size * 0.5;
            float extent = h.z > 0.0 ? h.z : 0.5;
            float dnorm = length(px - half_size) / (length(size) * extent);
            float f = 0.0;
            if (dnorm <= 1.0) {
                float k = cos(h.y * dnorm);
                f = k * k * k * k;
            }
            c.x = c.x * f;
            c.yz = (c.yz - 127.0 / 255.0) * f + 127.0 / 255.0;
        } else if (kind == 3) {
            c.yz = vec2(0.5);
        } else if (kind == 4) {
            vec4 r = ops[i * 4 + 1];
            vec4 g = ops[i * 4 + 2];
            vec4 b = ops[i * 4 + 3];
            c = clamp(vec3(dot(r.xyz, c) + r.w, dot(g.xyz, c) + g.w, dot(b.xyz, c) + b.w), 0.0, 1.0);
        }
    }
    return c;
}
"""

CLEAR = FRAGMENT_HEADER + """
void main() {
    fragColor = vec4(pad.rgb, 1.0);
}
"""

PREP = FRAGMENT_HEADER + OPS + """
vec3 sample_source(vec2 suv) {
    int mode = int(source.w + 0.5);
    if (mode == 0) {
        vec4 t = texture(tex0, suv);
        return state.w > 0.5 ? t.bgr : t.rgb;
    }
    float y = texture(tex0, suv).r * source.z;
    if (mode == 1) {
        vec2 uv = texture(tex1, suv).rg * source.z;
        return vec3(y, uv);
    }
    return vec3(y, texture(tex1, suv).r * source.z, texture(tex2, suv).r * source.z);
}

void main() {
    vec2 px = v_uv * target.xy;
    vec2 extent = fit.zw - fit.xy;
    vec2 fuv = (v_uv - fit.xy) / extent;
    vec3 c;
    if (fuv.x < 0.0 || fuv.y < 0.0 || fuv.x > 1.0 || fuv.y > 1.0) {
        c = pad.rgb;
    } else {
        int taps = int(target.w + 0.5);
        if (taps <= 1) {
            c = sample_source(fuv);
        } else {
            vec2 step = 1.0 / (target.xy * extent);
            vec3 total = vec3(0.0);
            for (int j = 0; j < 4; ++j) {
                if (j >= taps) {
                    break;
                }
                for (int i = 0; i < 4; ++i) {
                    if (i >= taps) {
                        break;
                    }
                    vec2 offset = (vec2(float(i), float(j)) + 0.5) / float(taps) - 0.5;
                    total += sample_source(clamp(fuv + offset * step, 0.0, 1.0));
                }
            }
            c = total / float(taps * taps);
        }
    }
    int space = int(state.x + 0.5);
    c = apply_ops(c, space, px, target.xy);
    c = to_space(c, space, int(state.y + 0.5));
    fragColor = vec4(c, 1.0);
}
"""

BLUR = FRAGMENT_HEADER + OPS + """
// Pas d'opérateurs bit à bit ni d'abs entier : la variante GLSL 1.20 (OpenGL de
// compatibilité, macOS) les refuse.
float weight_of(int which, int i) {
    int q = i / 4;
    int k = i - q * 4;
    vec4 w = which == 0 ? weights_luma[q] : weights_chroma[q];
    return k == 0 ? w.x : (k == 1 ? w.y : (k == 2 ? w.z : w.w));
}

void main() {
    vec2 texel = 1.0 / target.xy;
    vec2 dir = blur.xy * texel;
    int rl = int(blur.z + 0.5);
    int rc = int(blur.w + 0.5);
    vec4 center = texture(tex0, v_uv);
    float y = center.x * weight_of(0, 0);
    vec2 ch = center.yz * weight_of(1, 0);
    int r = rl > rc ? rl : rc;
    for (int i = 1; i <= 96; ++i) {
        if (i > r) {
            break;
        }
        vec4 a = texture(tex0, v_uv + dir * float(i));
        vec4 b = texture(tex0, v_uv - dir * float(i));
        if (i <= rl) {
            y += (a.x + b.x) * weight_of(0, i);
        }
        if (i <= rc) {
            ch += (a.yz + b.yz) * weight_of(1, i);
        }
    }
    if (rl == 0) {
        y = center.x;
    }
    if (rc == 0) {
        ch = center.yz;
    }
    vec3 c = vec3(y, ch);
    int space = int(state.x + 0.5);
    c = apply_ops(c, space, v_uv * target.xy, target.xy);
    c = to_space(c, space, int(state.y + 0.5));
    fragColor = vec4(c, 1.0);
}
"""

SHARPEN = FRAGMENT_HEADER + OPS + """
float binomial(int offset) {
    if (offset == 0) {
        return 6.0 / 16.0;
    }
    if (offset == 1 || offset == -1) {
        return 4.0 / 16.0;
    }
    return 1.0 / 16.0;
}

void main() {
    vec2 texel = 1.0 / target.xy;
    float blurred = 0.0;
    for (int j = -2; j <= 2; ++j) {
        for (int i = -2; i <= 2; ++i) {
            float w = binomial(i) * binomial(j);
            blurred += texture(tex0, v_uv + vec2(float(i), float(j)) * texel).x * w;
        }
    }
    vec3 c = texture(tex0, v_uv).xyz;
    c.x = clamp(c.x + misc.x * (c.x - blurred), 0.0, 1.0);
    int space = int(state.x + 0.5);
    c = apply_ops(c, space, v_uv * target.xy, target.xy);
    c = to_space(c, space, int(state.y + 0.5));
    fragColor = vec4(c, 1.0);
}
"""

COMPOSITE = FRAGMENT_HEADER + """
vec3 blend_colors(int mode, vec3 cb, vec3 cs) {
    if (mode == 1) {
        return cb * cs;
    } else if (mode == 2) {
        return cb + cs - cb * cs;
    } else if (mode == 3) {
        vec3 low = 2.0 * cb * cs;
        vec3 high = 1.0 - 2.0 * (1.0 - cb) * (1.0 - cs);
        return mix(high, low, step(cb, vec3(0.5)));
    } else if (mode == 4) {
        return min(cb, cs);
    } else if (mode == 5) {
        return max(cb, cs);
    } else if (mode == 6) {
        return min(vec3(1.0), cb + cs);
    } else if (mode == 7) {
        return abs(cb - cs);
    }
    return cs;
}

void main() {
    vec2 px = v_uv * target.xy;
    vec2 l = (inverse_map * vec4(px, 0.0, 1.0)).xy;
    vec2 luv = l / source.xy;
    vec3 dst = texture(tex2, v_uv).rgb;
    vec3 c = to_space(texture(tex0, luv).rgb, int(state.x + 0.5), 1);
    float coverage = clamp(min(min(l.x, source.x - l.x), min(l.y, source.y - l.y)) + 0.5, 0.0, 1.0);
    float alpha = coverage * comp.x;
    if (comp.z > 0.5) {
        alpha *= texture(tex1, luv).a;
    }
    vec3 blended = blend_colors(int(comp.y + 0.5), dst, clamp(c, 0.0, 1.0));
    fragColor = vec4(mix(dst, blended, alpha), 1.0);
}
"""

SHIFT = FRAGMENT_HEADER + OPS + """
// Aberration chromatique (rgbashift de l'export) : rouge lu à gauche, bleu à droite, bords recopiés.
vec3 rgb_at(vec2 uv) {
    return to_space(texture(tex0, uv).rgb, int(state.x + 0.5), 1);
}

void main() {
    vec2 d = vec2(misc.x / target.x, 0.0);
    vec3 c = vec3(rgb_at(v_uv - d).r, rgb_at(v_uv).g, rgb_at(v_uv + d).b);
    int space = 1;
    c = apply_ops(c, space, v_uv * target.xy, target.xy);
    c = to_space(c, space, int(state.y + 0.5));
    fragColor = vec4(c, 1.0);
}
"""

HAZE = FRAGMENT_HEADER + OPS + """
// Heat haze (geq de l'export) : la ligne glisse d'un nombre entier de pixels de l'export.
// misc : amplitude, fréquence, phase (t·vitesse), texels par pixel (x) ; reserved : haut, étendue, texels par pixel (y).
void main() {
    float row = v_uv.y * target.y / reserved.z - 0.5;
    float height = target.y / reserved.z;
    float ramp = clamp((row / height - reserved.x) / reserved.y, 0.0, 1.0);
    float shift = floor(misc.x * sin(row * misc.y + misc.z) * ramp) * misc.w;
    vec3 c = texture(tex0, v_uv - vec2(shift / target.x, 0.0)).rgb;
    int space = int(state.x + 0.5);
    c = apply_ops(c, space, v_uv * target.xy, target.xy);
    c = to_space(c, space, int(state.y + 0.5));
    fragColor = vec4(c, 1.0);
}
"""

GLOW = FRAGMENT_HEADER + OPS + """
// Bloom (split + lutrgb + gblur + blend addition de l'export).
// misc.x = 0 : halo = clamp((rgb − seuil) · gain) ; misc.x = 1 : image (tex0) + halo flouté (tex1).
void main() {
    vec3 rgb = to_space(texture(tex0, v_uv).rgb, int(state.x + 0.5), 1);
    if (misc.x < 0.5) {
        fragColor = vec4(clamp((rgb - vec3(misc.y)) * misc.z, 0.0, 1.0), 1.0);
        return;
    }
    vec3 c = clamp(rgb + texture(tex1, v_uv).rgb, 0.0, 1.0);
    int space = 1;
    c = apply_ops(c, space, v_uv * target.xy, target.xy);
    c = to_space(c, space, int(state.y + 0.5));
    fragColor = vec4(c, 1.0);
}
"""

GRADE = FRAGMENT_HEADER + """
// Étalonnage de l'export (eq, colorbalance, courbes, LUT .cube) cuit en LUT 3D par FFmpeg (core/gpu_grade.py).
// tex1 : atlas de N tranches N×N (x = c2·N + c1, y = c0) ; misc : N, espace de la LUT (0 YUV, 1 RVB).
// Interpolation trilinéaire : bilinéaire matérielle dans une tranche, puis entre les deux tranches voisines.
vec3 lut_slice(float slice, vec2 c10, float n) {
    vec2 uv = vec2((slice * n + c10.x * (n - 1.0) + 0.5) / (n * n), (c10.y * (n - 1.0) + 0.5) / n);
    return texture(tex1, uv).rgb;
}

void main() {
    vec3 c = clamp(to_space(texture(tex0, v_uv).rgb, int(state.x + 0.5), int(misc.y + 0.5)), 0.0, 1.0);
    float n = misc.x;
    float z = c.z * (n - 1.0);
    float z0 = floor(z);
    float z1 = min(z0 + 1.0, n - 1.0);
    vec3 rgb = mix(lut_slice(z0, c.yx, n), lut_slice(z1, c.yx, n), z - z0);
    fragColor = vec4(to_space(rgb, 1, int(state.y + 0.5)), 1.0);
}
"""

PRESENT = FRAGMENT_HEADER + """
void main() {
    vec2 extent = fit.zw - fit.xy;
    vec2 cuv = (v_uv - fit.xy) / extent;
    if (cuv.x < 0.0 || cuv.y < 0.0 || cuv.x > 1.0 || cuv.y > 1.0) {
        fragColor = vec4(pad.rgb, 1.0);
    } else {
        fragColor = vec4(texture(tex0, cuv).rgb, 1.0);
    }
}
"""

SHADERS: dict[str, tuple[str, str]] = {
    "quad.vert": ("vert", VERTEX),
    "clear.frag": ("frag", CLEAR),
    "prep.frag": ("frag", PREP),
    "blur.frag": ("frag", BLUR),
    "sharpen.frag": ("frag", SHARPEN),
    "shift.frag": ("frag", SHIFT),
    "haze.frag": ("frag", HAZE),
    "glow.frag": ("frag", GLOW),
    "grade.frag": ("frag", GRADE),
    "composite.frag": ("frag", COMPOSITE),
    "present.frag": ("frag", PRESENT),
}
"""Nom du fichier ``.qsb`` (sans l'extension) → (étape, source)."""

from nodecanvas_cli.csharp import scan

SRC = r"""
using UnityEngine;
using System.Collections.Generic;

namespace Game.AI
{
    // public int commented;
    /* public int alsoCommented; */
    [Name("Fancy Task"), Category("Tests")]
    [Description("Does \"things\"; { not a block }")]
    public class FancyTask : ActionTask
    {
        public BBParameter<float> speed = 2.5f, other;
        public Mode mode = Mode.Fast;
        [SerializeField] private string _secret = "a;b{c}";
        private int hidden;
        [System.NonSerialized] public int skipped;
        public static int counter;
        public const int Max = 3;
        [fsSerializeAs("renamed")] private bool _flag;
        public List<int> numbers = new List<int> { 1, 2 };
        public System.Action callback = () => { Debug.Log("x"); };

        public enum Mode { Slow, Fast = 4, Faster }

        [System.Flags]
        public enum Mask { None = 0, A = 1 << 0, B = 1 << 1, AB = A | B }

        public float Speed => speed.value;
        public bool Ready { get { return true; } }
        public override int maxOutConnections { get { return 2; } }
        public override int maxInConnections => -1;

        protected override string info {
            get { return "x"; }
        }

        public void Run(Vector3 where, ref int count, params float[] rest) { if (count > 0) { count--; } }
        public bool Check(GameObject go) => go != null;
        public T Get<T>(string key) where T : class { return null; }

        class Nested { public int inner; }
    }
}
"""


def decls():
    return {d.full_name: d for d in scan(SRC, "Fancy.cs")}


def test_class_and_attributes():
    d = decls()["Game.AI.FancyTask"]
    assert d.bases == ["ActionTask"]
    assert d.attr_str("Name") == "Fancy Task"
    assert d.attr_str("Category") == "Tests"
    assert d.attr_str("Description") == 'Does "things"; { not a block }'
    assert "UnityEngine" in d.usings


def test_fields_and_serialization_rules():
    d = decls()["Game.AI.FancyTask"]
    fields = {f.name: f for f in d.fields}
    assert fields["speed"].init == "2.5f" and fields["speed"].type == "BBParameter<float>"
    assert fields["other"].type == "BBParameter<float>" and fields["other"].init is None
    assert fields["_secret"].init == '"a;b{c}"'
    assert fields["numbers"].init.startswith("new List<int>")
    serialized = {f.serialized_name for f in d.fields if f.serialized}
    assert {"speed", "other", "mode", "_secret", "renamed", "numbers", "callback"} <= serialized
    assert not {"hidden", "skipped", "counter", "Max", "commented", "alsoCommented"} & serialized


def test_enums():
    ds = decls()
    mode = ds["Game.AI.FancyTask+Mode"]
    assert mode.enum_values() == {0: "Slow", 4: "Fast", 5: "Faster"}
    mask = ds["Game.AI.FancyTask+Mask"]
    assert mask.is_flags
    assert mask.enum_values()[3] == "AB"
    assert mask.enum_name(2) == "B"


def test_properties_methods_consts():
    d = decls()["Game.AI.FancyTask"]
    assert d.properties["Speed"] == "float"
    assert "Ready" in d.properties
    assert d.const_props == {"Ready": "true", "maxOutConnections": "2", "maxInConnections": "-1"}
    assert d.methods["Run"][0]["params"] == ["Vector3", "int", "float[]"]
    assert d.methods["Check"][0]["ret"] == "bool"
    assert "Get" in d.methods
    assert "Nested" not in d.methods


def test_nested_class_name():
    assert "Game.AI.FancyTask+Nested" in decls()


def test_file_scoped_namespace_and_generics():
    src = "namespace A.B;\npublic class Box<T, U> : Base<T> { public T item; }\n"
    (d,) = scan(src)
    assert d.full_name == "A.B.Box`2"
    assert d.bases == ["Base<T>"]
    assert d.fields[0].name == "item"

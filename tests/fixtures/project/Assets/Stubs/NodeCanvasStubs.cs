// Test stubs: declarations mirroring the public serialized surface of NodeCanvas types,
// so nodecanvas-cli's source scanner can be tested without the (commercial) NodeCanvas sources.
// No behaviour is implemented here.

using System.Collections.Generic;
using UnityEngine;

namespace ParadoxNotion
{
    public enum CompareMethod { EqualTo, GreaterThan, LessThan, GreaterOrEqualTo, LessOrEqualTo }
    public enum OperationMethod { Set, Add, Subtract, Multiply, Divide }
}

namespace NodeCanvas.Framework
{
    public enum BooleanStatus { Failure = 0, Success = 1 }

    public class BBParameter { }
    public class BBParameter<T> : BBParameter { }
    public class BBObjectParameter : BBParameter { }
    public class BBMappingParameter : BBObjectParameter
    {
        [SerializeField] private string _targetSubGraphVariableID;
        [SerializeField] private bool _canRead;
        [SerializeField] private bool _canWrite;
    }

    public abstract class Graph : ScriptableObject { }

    public abstract partial class Node
    {
        protected class AutoSortWithChildrenConnections : System.Attribute { }

        [SerializeField] private string _UID;
        [SerializeField] private string _name;
        [SerializeField] private string _tag;
        [SerializeField] private Vector2 _position;
        [SerializeField] private string _comment;
        [SerializeField] private bool _isBreakpoint;

        abstract public int maxInConnections { get; }
        abstract public int maxOutConnections { get; }
    }

    public class NodeReference<T> where T : Node
    {
        [SerializeField] private string _targetNodeUID;
    }

    public abstract class Connection
    {
        [SerializeField] private Node _sourceNode;
        [SerializeField] private Node _targetNode;
        [SerializeField] private string _UID;
        [SerializeField] private bool _isDisabled;
    }

    public abstract class Task
    {
        [fsSerializeAs("_isDisabled")]
        private bool _isUserDisabled;
        [fsSerializeAs("overrideAgent")]
        internal protected TaskAgentParameter _agentParameter;
    }

    public class TaskAgentParameter : BBParameter { }

    public abstract class ActionTask : Task { }

    public abstract class ConditionTask : Task
    {
        [SerializeField] private bool _invert;
    }

    public class ActionList : ActionTask
    {
        public enum ActionsExecutionMode { ActionsRunInSequence, ActionsRunInParallel }
        public ActionsExecutionMode executionMode;
        public List<ActionTask> actions = new List<ActionTask>();
    }

    public class ConditionList : ConditionTask
    {
        public enum ConditionsCheckMode { AllTrueRequired, AnyTrueSuffice }
        public ConditionsCheckMode checkMode;
        public List<ConditionTask> conditions = new List<ConditionTask>();
    }
}

namespace NodeCanvas.Framework.Internal
{
    public class MissingAction : NodeCanvas.Framework.ActionTask
    {
        public string _missingType;
        public string _recoveryState;
    }
}

namespace NodeCanvas.BehaviourTrees
{
    using NodeCanvas.Framework;

    public class BehaviourTree : Graph { }

    public class BTConnection : Connection { }

    public abstract class BTNode : Node
    {
        public override int maxInConnections { get { return 1; } }
        public override int maxOutConnections { get { return 0; } }
    }

    public abstract class BTComposite : BTNode
    {
        sealed public override int maxOutConnections => -1;
    }

    public abstract class BTDecorator : BTNode
    {
        sealed public override int maxOutConnections { get { return 1; } }
    }

    public abstract class BTNodeNested<T> : BTNode where T : Graph
    {
        [SerializeField] private List<BBMappingParameter> _variablesMap;
    }

    [Name("Sequencer")]
    public class Sequencer : BTComposite
    {
        public bool dynamic;
        public bool random;
    }

    public class Selector : BTComposite
    {
        public bool dynamic;
        public bool random;
    }

    public class ProbabilitySelector : BTComposite
    {
        [AutoSortWithChildrenConnections]
        public List<BBParameter<float>> childWeights;
        public BBParameter<float> failChance;
    }

    public class BinarySelector : BTNode
    {
        public bool dynamic;
        [SerializeField] private ConditionTask _condition;
        public override int maxOutConnections => 2;
    }

    public class ActionNode : BTNode
    {
        [SerializeField] private ActionTask _action;
    }

    public class ConditionNode : BTNode
    {
        [SerializeField] private ConditionTask _condition;
    }

    [Name("Cooldown")]
    [Description("Test stub of a cooldown decorator.")]
    public class Cooldown : BTDecorator
    {
        public BBParameter<float> coolDownTime = 1f;
    }

    [Name("Repeat")]
    public class Repeater : BTDecorator
    {
        public enum RepeaterMode { RepeatTimes = 0, RepeatUntil = 1, RepeatForever = 2 }
        public RepeaterMode repeaterMode = RepeaterMode.RepeatTimes;
        public BBParameter<int> repeatTimes = 1;
    }

    public class Merge : BTDecorator
    {
        public override int maxInConnections => -1;
    }

    [Name("Sub Tree")]
    public class SubTree : BTNodeNested<BehaviourTree>
    {
        [SerializeField] private BBParameter<BehaviourTree> _subTree = null;
    }
}

namespace NodeCanvas.StateMachines
{
    using NodeCanvas.Framework;
    using NodeCanvas.BehaviourTrees;

    public class FSM : Graph { }

    public class FSMConnection : Connection
    {
        [SerializeField] private ConditionTask _condition;
    }

    public abstract class FSMNode : Node
    {
        public override int maxInConnections { get { return -1; } }
        public override int maxOutConnections { get { return -1; } }
    }

    public abstract class FSMState : FSMNode
    {
        public enum TransitionEvaluationMode { CheckContinuously, CheckAfterStateFinished, CheckManually }
        [SerializeField] private TransitionEvaluationMode _transitionEvaluation;
    }

    public abstract class FSMStateNested<T> : FSMState where T : Graph
    {
        [SerializeField] private List<BBMappingParameter> _variablesMap;
    }

    public class ActionState : FSMState
    {
        [SerializeField] private ActionList _actionList;
        [SerializeField] private bool _repeatStateActions;
    }

    [Name("Sub BehaviourTree")]
    public class NestedBTState : FSMStateNested<BehaviourTree>
    {
        public enum BTExitMode { StopAndRestart, PauseAndResume }
        public enum BTExecutionMode { Once, Repeat }

        [SerializeField] private BBParameter<BehaviourTree> _nestedBT = null;
        public BTExitMode exitMode = BTExitMode.StopAndRestart;
        public BTExecutionMode executionMode = BTExecutionMode.Repeat;
    }

    public class AnyState : FSMNode
    {
        public bool dontRetriggerStates = false;
        public override int maxInConnections { get { return 0; } }
    }
}

namespace NodeCanvas.DialogueTrees
{
    using NodeCanvas.Framework;

    public class DialogueTree : Graph { }

    public class DTConnection : Connection { }

    public enum Locales { Default = 0, Chinese, English, French, German, Greek, Italian, Japanese, Russian, Spanish }

    public class Statement
    {
        public class Localization
        {
            public string text;
            public AudioClip audio;
        }

        [SerializeField] private string _text = string.Empty;
        [SerializeField] private AudioClip _audio;
        [SerializeField] private string _meta = string.Empty;
        [SerializeField] private Dictionary<Locales, Localization> _localizations;
    }

    public abstract class DTNode : Node
    {
        [SerializeField] private string _actorName = "SELF";
        [SerializeField] private string _actorParameterID;
        public override int maxInConnections { get { return -1; } }
        public override int maxOutConnections { get { return 1; } }
    }

    public class StatementNode : DTNode
    {
        [SerializeField] public Statement statement = new Statement();
    }

    public class MultipleChoiceNode : DTNode
    {
        public class Choice
        {
            public bool isUnfolded = true;
            public Statement statement;
            public ConditionTask condition;
        }

        public float availableTime;
        public bool saySelection;
        [SerializeField, AutoSortWithChildrenConnections]
        private List<Choice> availableChoices = new List<Choice>();

        public override int maxOutConnections => availableChoices.Count;
    }

    public class ConditionNode : DTNode
    {
        [SerializeField] private ConditionTask _condition;
        public override int maxOutConnections { get { return 2; } }
    }

    public class FinishNode : DTNode
    {
        public BooleanStatus finishState = BooleanStatus.Success;
        public override int maxOutConnections { get { return 0; } }
    }

    public class Jumper : DTNode
    {
        [fsSerializeAs("_sourceNodeUID")]
        public NodeReference<DTNode> _targetNode;
        public override int maxOutConnections { get { return 0; } }
    }
}

namespace NodeCanvas.Tasks.Actions
{
    using NodeCanvas.Framework;
    using ParadoxNotion;

    public class Wait : ActionTask
    {
        public BBParameter<float> waitTime = 1f;
        public BooleanStatus finishStatus = BooleanStatus.Success;
    }

    public class SetBoolean : ActionTask
    {
        public enum BoolSetModes { False = 0, True = 1, Toggle = 2 }
        [BlackboardOnly] public BBParameter<bool> boolVariable;
        public BoolSetModes setTo = BoolSetModes.True;
    }

    public class SetFloat : ActionTask
    {
        [BlackboardOnly] public BBParameter<float> valueA;
        public OperationMethod Operation = OperationMethod.Set;
        public BBParameter<float> valueB;
        public bool perSecond;
    }

    public class SetVariable<T> : ActionTask
    {
        [BlackboardOnly] public BBParameter<T> valueA;
        public BBParameter<T> valueB;
    }

    public class ExecuteFunction_Multiplatform : ActionTask
    {
        [SerializeField] protected SerializedMethodInfo method;
        [SerializeField] protected List<BBObjectParameter> parameters = new List<BBObjectParameter>();
        [SerializeField, BlackboardOnly] protected BBObjectParameter returnValue;
    }

    public class GetProperty_Multiplatform : ActionTask
    {
        [SerializeField] protected SerializedMethodInfo method;
        [SerializeField, BlackboardOnly] protected BBObjectParameter returnValue;
    }

    public class SetProperty_Multiplatform : ActionTask
    {
        [SerializeField] protected SerializedMethodInfo method;
        [SerializeField] protected BBObjectParameter parameter;
    }

    public class GetField : ActionTask
    {
        [SerializeField] protected SerializedFieldInfo field;
        [SerializeField, BlackboardOnly] protected BBObjectParameter saveAs;
    }

    public class SetField : ActionTask
    {
        [SerializeField] protected SerializedFieldInfo field;
        [SerializeField] protected BBObjectParameter setValue;
    }

}

namespace NodeCanvas.Tasks.Conditions
{
    using NodeCanvas.Framework;
    using ParadoxNotion;

    public class CheckBoolean : ConditionTask
    {
        [BlackboardOnly] public BBParameter<bool> valueA;
        public BBParameter<bool> valueB = true;
    }

    public class CheckFloat : ConditionTask
    {
        [BlackboardOnly] public BBParameter<float> valueA;
        public CompareMethod checkType = CompareMethod.EqualTo;
        public BBParameter<float> valueB;
    }

    public class CheckFunction_Multiplatform : ConditionTask
    {
        [SerializeField] protected SerializedMethodInfo method;
        [SerializeField] protected List<BBObjectParameter> parameters = new List<BBObjectParameter>();
        [SerializeField] protected CompareMethod comparison;
        [SerializeField] protected BBObjectParameter checkValue;
    }

    public class CheckProperty_Multiplatform : ConditionTask
    {
        [SerializeField] protected SerializedMethodInfo method;
        [SerializeField] protected BBObjectParameter checkValue;
        [SerializeField] protected CompareMethod comparison;
    }

    public class CheckField : ConditionTask
    {
        [SerializeField] protected BBObjectParameter checkValue;
        [SerializeField] protected CompareMethod comparison;
        [SerializeField] protected SerializedFieldInfo field;
    }
}

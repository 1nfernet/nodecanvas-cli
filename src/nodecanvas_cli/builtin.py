"""Built-in knowledge used when a project's sources do not contain a type.

``STUBS`` declares the serialized surface (fields, defaults, enums, connection limits) of the most
common NodeCanvas types, in C#, so the regular source scanner reads it. It holds no NodeCanvas code.
Project sources always win: these declarations are consulted only for types the project lacks.
"""

from __future__ import annotations

BUILTIN_FILE = "<nodecanvas-cli builtin>"

STUBS = r"""
using NodeCanvas.Framework;
using ParadoxNotion;

namespace ParadoxNotion
{
    public enum CompareMethod { EqualTo, GreaterThan, LessThan, GreaterOrEqualTo, LessOrEqualTo }
    public enum OperationMethod { Set, Add, Subtract, Multiply, Divide }
}

namespace NodeCanvas.Framework
{
    public enum BooleanStatus { Failure = 0, Success = 1 }
    public enum FinalStatus { Failure = 0, Success = 1, Optional = 5 }

    public class BBParameter { }
    public class BBParameter<T> : BBParameter { }
    public class BBObjectParameter : BBParameter { }
    public class BBMappingParameter : BBObjectParameter
    {
        [SerializeField] private string _targetSubGraphVariableID;
        [SerializeField] private bool _canRead;
        [SerializeField] private bool _canWrite;
    }

    public abstract class Graph : UnityEngine.ScriptableObject { }

    public abstract class Node
    {
        [SerializeField] private string _UID;
        [SerializeField] private string _name;
        [SerializeField] private string _tag;
        [SerializeField] private Vector2 _position;
        [SerializeField] private string _comment;
        [SerializeField] private bool _isBreakpoint;
        abstract public int maxInConnections { get; }
        abstract public int maxOutConnections { get; }
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
        [fsSerializeAs("_isDisabled")] private bool _isUserDisabled;
        [fsSerializeAs("overrideAgent")] internal protected TaskAgentParameter _agentParameter;
    }

    public class TaskAgentParameter : BBParameter { }
    public abstract class ActionTask : Task { }
    public abstract class ConditionTask : Task
    {
        [SerializeField] private bool _invert;
    }
    public abstract class ActionTask<T> : ActionTask { }
    public abstract class ConditionTask<T> : ConditionTask { }

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

    public abstract class GraphOwner : UnityEngine.MonoBehaviour { }
    public abstract class GraphOwner<T> : GraphOwner { }
    public class Blackboard : UnityEngine.MonoBehaviour { }
    public class GlobalBlackboard : Blackboard { }
    public class AssetBlackboard : UnityEngine.ScriptableObject { }
}

namespace NodeCanvas.BehaviourTrees
{
    public class BehaviourTree : NodeCanvas.Framework.Graph { }
    public class BehaviourTreeOwner : GraphOwner<BehaviourTree> { }
    public class BTConnection : Connection { }

    public abstract class BTNode : Node
    {
        public override int maxInConnections { get { return 1; } }
        public override int maxOutConnections { get { return 0; } }
    }
    public abstract class BTComposite : BTNode { sealed public override int maxOutConnections => -1; }
    public abstract class BTDecorator : BTNode { sealed public override int maxOutConnections { get { return 1; } } }
    public abstract class BTNodeNested<T> : BTNode
    {
        [SerializeField] private List<BBMappingParameter> _variablesMap;
    }

    public class ActionNode : BTNode { [SerializeField] private ActionTask _action; }
    public class ConditionNode : BTNode { [SerializeField] private ConditionTask _condition; }
    [Name("Sub Tree")] public class SubTree : BTNodeNested<BehaviourTree>
    {
        [SerializeField] private BBParameter<BehaviourTree> _subTree = null;
    }

    public class Sequencer : BTComposite { public bool dynamic; public bool random; }
    public class Selector : BTComposite { public bool dynamic; public bool random; }
    [Name("Step Sequencer")] public class StepIterator : BTComposite { }
    [Name("Parallel")] public class Parallel : BTComposite
    {
        public enum ParallelPolicy { FirstFailure = 0, FirstSuccess = 1, FirstSuccessOrFailure = 2 }
        public ParallelPolicy policy = ParallelPolicy.FirstFailure;
        public bool dynamic;
    }
    public class ProbabilitySelector : BTComposite
    {
        [AutoSortWithChildrenConnections] public List<BBParameter<float>> childWeights;
        public BBParameter<float> failChance;
    }
    public class BinarySelector : BTNode
    {
        public bool dynamic;
        [SerializeField] private ConditionTask _condition;
        public override int maxOutConnections => 2;
    }

    [Name("Conditional")] public class ConditionalEvaluator : BTDecorator
    {
        public bool isDynamic;
        public FinalStatus conditionFailReturn = FinalStatus.Failure;
        [SerializeField] private ConditionTask _condition;
    }
    public class WaitUntil : BTDecorator { [SerializeField] private ConditionTask _condition; }
    public class Cooldown : BTDecorator
    {
        public BBParameter<float> coolDownTime = 5f;
        public FinalStatus coolingStatus = FinalStatus.Optional;
    }
    [Name("Repeat")] public class Repeater : BTDecorator
    {
        public enum RepeaterMode { RepeatTimes = 0, RepeatUntil = 1, RepeatForever = 2 }
        public RepeaterMode repeaterMode = RepeaterMode.RepeatTimes;
        public BBParameter<int> repeatTimes = 1;
        public BooleanStatus repeatUntilStatus = BooleanStatus.Success;
    }
    [Name("Iterate")] public class Iterator : BTDecorator
    {
        public enum TerminationConditions { None, FirstSuccess, FirstFailure }
        [RequiredField, BlackboardOnly] public BBParameter<IList> targetList;
        [BlackboardOnly] public BBObjectParameter current;
        [BlackboardOnly] public BBParameter<int> storeIndex;
        public TerminationConditions terminationCondition = TerminationConditions.None;
        public BBParameter<int> maxIteration = -1;
        public bool resetIndex = true;
    }
    [Name("Invert")] public class Inverter : BTDecorator { }
    public class Optional : BTDecorator { }
    public class Succeed : BTDecorator { }
    public class Merge : BTDecorator { public override int maxInConnections => -1; }
}

namespace NodeCanvas.StateMachines
{
    public class FSM : NodeCanvas.Framework.Graph { }
    public class FSMOwner : GraphOwner<FSM> { }
    public class FSMConnection : Connection { [SerializeField] private ConditionTask _condition; }
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
    public abstract class FSMStateNested<T> : FSMState
    {
        [SerializeField] private List<BBMappingParameter> _variablesMap;
    }
    [Name("Action State")] public class ActionState : FSMState
    {
        [SerializeField] private ActionList _actionList;
        [SerializeField] private bool _repeatStateActions;
    }
    [Name("Sub BehaviourTree")] public class NestedBTState : FSMStateNested<BehaviourTree>
    {
        public enum BTExitMode { StopAndRestart, PauseAndResume }
        public enum BTExecutionMode { Once, Repeat }
        [SerializeField] private BBParameter<BehaviourTree> _nestedBT = null;
        public BTExitMode exitMode = BTExitMode.StopAndRestart;
        public BTExecutionMode executionMode = BTExecutionMode.Repeat;
        public string successEvent;
        public string failureEvent;
    }
    public class AnyState : FSMNode
    {
        public bool dontRetriggerStates = false;
        public override int maxInConnections { get { return 0; } }
    }
}

namespace NodeCanvas.DialogueTrees
{
    public class DialogueTree : NodeCanvas.Framework.Graph { }
    public class DialogueTreeController : GraphOwner<DialogueTree> { }
    public class DTConnection : Connection { }
    public enum Locales { Default = 0, Chinese, English, French, German, Greek, Italian, Japanese, Russian, Spanish }
    public class Statement
    {
        public class Localization { public string text; public AudioClip audio; }
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
    [Name("Say")] public class StatementNode : DTNode { [SerializeField] public Statement statement = new Statement(); }
    [Name("Multiple Choice")] public class MultipleChoiceNode : DTNode
    {
        public class Choice
        {
            public bool isUnfolded = true; public Statement statement; public ConditionTask condition;
        }
        public float availableTime;
        public bool saySelection;
        [SerializeField, AutoSortWithChildrenConnections] private List<Choice> availableChoices = new List<Choice>();
    }
    [Name("Condition")] public class ConditionNode : DTNode
    {
        [SerializeField] private ConditionTask _condition;
        public override int maxOutConnections { get { return 2; } }
    }
    [Name("Task Action")] public class ActionNode : DTNode { [SerializeField] private ActionTask _action; }
    [Name("FINISH")] public class FinishNode : DTNode
    {
        public BooleanStatus finishState = BooleanStatus.Success;
        public override int maxOutConnections { get { return 0; } }
    }
    [Name("JUMP")] public class Jumper : DTNode
    {
        [fsSerializeAs("_sourceNodeUID")] public NodeReference<DTNode> _targetNode;
        public override int maxOutConnections { get { return 0; } }
    }
}

namespace NodeCanvas.Tasks.Actions
{
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
    public class SetInt : ActionTask
    {
        [BlackboardOnly] public BBParameter<int> valueA;
        public OperationMethod Operation = OperationMethod.Set;
        public BBParameter<int> valueB;
    }
    public class SetVector3 : ActionTask
    {
        [BlackboardOnly] public BBParameter<Vector3> valueA;
        public OperationMethod operation;
        public BBParameter<Vector3> valueB;
        public bool perSecond;
    }
    public class SetFloatRandom : ActionTask
    {
        public BBParameter<float> minValue;
        public BBParameter<float> maxValue;
        [BlackboardOnly] public BBParameter<float> floatVariable;
    }
    public class SetVariable<T> : ActionTask
    {
        [BlackboardOnly] public BBParameter<T> valueA;
        public BBParameter<T> valueB;
    }
    public class GetListCount : ActionTask
    {
        [RequiredField, BlackboardOnly] public BBParameter<IList> targetList;
        [BlackboardOnly] public BBParameter<int> saveAs;
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
        public float differenceThreshold = 0.05f;
    }
    public class CheckInt : ConditionTask
    {
        [BlackboardOnly] public BBParameter<int> valueA;
        public CompareMethod checkType = CompareMethod.EqualTo;
        public BBParameter<int> valueB;
    }
    public class CheckNull : ConditionTask { [BlackboardOnly] public BBParameter<System.Object> variable; }
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
"""

# Public instance members every UnityEngine.Object / Component / Behaviour / MonoBehaviour subclass inherits.
UNITY_BASE_MEMBERS = {
    "name",
    "hideFlags",
    "GetInstanceID",
    "ToString",
    "Equals",
    "GetHashCode",
    "GetType",
    "gameObject",
    "transform",
    "tag",
    "CompareTag",
    "GetComponent",
    "GetComponents",
    "GetComponentInChildren",
    "GetComponentsInChildren",
    "GetComponentInParent",
    "GetComponentsInParent",
    "TryGetComponent",
    "SendMessage",
    "SendMessageUpwards",
    "BroadcastMessage",
    "enabled",
    "isActiveAndEnabled",
    "StartCoroutine",
    "StopCoroutine",
    "StopAllCoroutines",
    "Invoke",
    "InvokeRepeating",
    "CancelInvoke",
    "IsInvoking",
    "useGUILayout",
    "runInEditMode",
    "destroyCancellationToken",
    "didStart",
    "didAwake",
}
UNITY_BASES = {"Object", "Component", "Behaviour", "MonoBehaviour", "ScriptableObject", "StateMachineBehaviour"}

import warnings

import matplotlib.pyplot as plt
import numpy as np
import scipy.cluster.vq
import scipy.optimize
import scipy.special
import scipy.stats
import sklearn
import sklearn.isotonic
import sklearn.linear_model
import sklearn.multiclass
import sklearn.utils
from sklearn.base import clone
from sklearn.utils.validation import check_is_fitted

# Ignore FutureWarnings
warnings.simplefilter(action='ignore', category=FutureWarning)

# Turn off tensorflow deprecation warnings
try:
    from tensorflow.python.util import module_wrapper as deprecation
except ImportError:
    from tensorflow.python.util import deprecation_wrapper as deprecation
deprecation._PER_MODULE_WARNING_LIMIT = 0


class CalibrationMethod(sklearn.base.BaseEstimator):
    """
    A generic class for probability calibration
    A calibration method takes a set of posterior class probabilities and transform them into calibrated posterior
    probabilities. Calibrated in this sense means that the empirical frequency of a correct class prediction matches its
    predicted posterior probability.
    """

    def __init__(self):
        super().__init__()

    def fit(self, X, y):
        """
        Fit the calibration method based on the given uncalibrated class probabilities X and ground truth labels y.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            Training data, i.e. predicted probabilities of the base classifier on the calibration set.
        y : array-like, shape (n_samples,)
            Target classes.
        Returns
        -------
        self : object
            Returns an instance of self.
        """
        raise NotImplementedError("Subclass must implement this method.")

    def predict_proba(self, X):
        """
        Compute calibrated posterior probabilities for a given array of posterior probabilities from an arbitrary
        classifier.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            The uncalibrated posterior probabilities.
        Returns
        -------
        P : array, shape (n_samples, n_classes)
            The predicted probabilities.
        """
        raise NotImplementedError("Subclass must implement this method.")

    def predict(self, X):
        """
        Predict the class of new samples after scaling. Predictions are identical to the ones from the uncalibrated
        classifier.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            The uncalibrated posterior probabilities.
        Returns
        -------
        C : array, shape (n_samples,)
            The predicted classes.
        """
        return np.argmax(self.predict_proba(X), axis=1)

    def plot(self, filename, xlim=[0, 1], **kwargs):
        """
        Plot the calibration map.
        Parameters
        ----------
        xlim : array-like
            Range of inputs of the calibration map to be plotted.
        **kwargs :
            Additional arguments passed on to :func:`matplotlib.plot`.
        """
        # TODO: Fix this plotting function

        # Generate data and transform
        x = np.linspace(0, 1, 10000)
        y = self.predict_proba(np.column_stack([1 - x, x]))[:, 1]

        # Plot and label
        plt.plot(x, y, **kwargs)
        plt.xlim(xlim)
        plt.xlabel("p(y=1|x)")
        plt.ylabel("f(p(y=1|x))")


class NoCalibration(CalibrationMethod):
    """
    A class that performs no calibration.
    This class can be used as a baseline for benchmarking.
    logits : bool, default=False
        Are the inputs for calibration logits (e.g. from a neural network)?
    """

    def __init__(self, logits=False):
        self.logits = logits

    def fit(self, X, y):
        return self

    def predict_proba(self, X):
        if self.logits:
            return scipy.special.softmax(X, axis=1)
        else:
            return X


class TemperatureScaling(CalibrationMethod):
    """
    Probability calibration using temperature scaling
    Temperature scaling [1]_ is a one parameter multi-class scaling method. Output confidence scores are calibrated,
    meaning they match empirical frequencies of the associated class prediction. Temperature scaling does not change the
    class predictions of the underlying model.
    Parameters
    ----------
    T_init : float
        Initial temperature parameter used for scaling. This parameter is optimized in order to calibrate output
        probabilities.
    verbose : bool
        Print information on optimization procedure.
    References
    ----------
    .. [1] On calibration of modern neural networks, C. Guo, G. Pleiss, Y. Sun, K. Weinberger, ICML 2017
    """

    def __init__(self, T_init=1, verbose=False):
        super().__init__()
        if T_init <= 0:
            raise ValueError("Temperature not greater than 0.")
        self.T_init = T_init
        self.verbose = verbose

    def fit(self, X, y):
        """
        Fit the calibration method based on the given uncalibrated class probabilities or logits X and ground truth
        labels y.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            Training data, i.e. predicted probabilities or logits of the base classifier on the calibration set.
        y : array-like, shape (n_samples,)
            Target classes.
        Returns
        -------
        self : object
            Returns an instance of self.
        """
        # Define objective function (NLL / cross entropy)
        def objective(T):
            # Calibrate with given T
            P = scipy.special.softmax(X / T, axis=1)

            # Compute negative log-likelihood
            P_y = P[np.array(np.arange(0, X.shape[0])), y]
            tiny = np.finfo(np.float).tiny  # to avoid division by 0 warning
            NLL = - np.sum(np.log(P_y + tiny))
            return NLL

        # Derivative of the objective with respect to the temperature T
        def gradient(T):
            # Exponential terms
            E = np.exp(X / T)

            # Gradient
            dT_i = (np.sum(E * (X - X[np.array(np.arange(0, X.shape[0])), y].reshape(-1, 1)), axis=1)) \
                   / np.sum(E, axis=1)
            grad = - dT_i.sum() / T ** 2
            return grad

        # Optimize
        self.T = scipy.optimize.fmin_bfgs(f=objective, x0=self.T_init,
                                          fprime=gradient, gtol=1e-06, disp=self.verbose)[0]

        # Check for T > 0
        if self.T <= 0:
            raise ValueError("Temperature not greater than 0.")

        return self

    def predict_proba(self, X):
        """
        Compute calibrated posterior probabilities for a given array of posterior probabilities from an arbitrary
        classifier.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            The uncalibrated posterior probabilities.
        Returns
        -------
        P : array, shape (n_samples, n_classes)
            The predicted probabilities.
        """
        # Check is fitted
        check_is_fitted(self, "T")

        # Transform with scaled softmax
        return scipy.special.softmax(X / self.T, axis=1)

    def latent(self, z):
        """
        Evaluate the latent function Tz of temperature scaling.
        Parameters
        ----------
        z : array-like, shape=(n_evaluations,)
            Input confidence for which to evaluate the latent function.
        Returns
        -------
        f : array-like, shape=(n_evaluations,)
            Values of the latent function at z.
        """
        check_is_fitted(self, "T")
        return self.T * z

    def plot_latent(self, z, filename, **kwargs):
        """
        Plot the latent function of the calibration method.
        Parameters
        ----------
        z : array-like, shape=(n_evaluations,)
            Input confidence to plot latent function for.
        filename :
            Filename / -path where to save output.
        kwargs
            Additional arguments passed on to matplotlib.pyplot.subplots.
        Returns
        -------
        """
        pass


class PlattScaling(CalibrationMethod):
    """
    Probability calibration using Platt scaling
    Platt scaling [1]_ [2]_ is a parametric method designed to output calibrated posterior probabilities for (non-probabilistic)
    binary classifiers. It was originally introduced in the context of SVMs. It works by fitting a logistic
    regression model to the model output using the negative log-likelihood as a loss function.
    Parameters
    ----------
    regularization : float, default=10^(-12)
        Regularization constant, determining degree of regularization in logistic regression.
    random_state : int, RandomState instance or None, optional (default=None)
        The seed of the pseudo random number generator to use when shuffling the data.
        If `int`, `random_state` is the seed used by the random number generator;
        If `RandomState` instance, `random_state` is the random number generator;
        If `None`, the random number generator is the RandomState instance used
        by `np.random`.
    References
    ----------
    .. [1] Platt, J. C. Probabilistic Outputs for Support Vector Machines and Comparisons to Regularized Likelihood
           Methods in Advances in Large-Margin Classifiers (MIT Press, 1999)
    .. [2] Lin, H.-T., Lin, C.-J. & Weng, R. C. A note on Platt’s probabilistic outputs for support vector machines.
           Machine learning 68, 267–276 (2007)
    """

    def __init__(self, regularization=10 ** -12, random_state=None):
        super().__init__()
        self.regularization = regularization
        self.random_state = sklearn.utils.check_random_state(random_state)

    def fit(self, X, y, n_jobs=None):
        """
        Fit the calibration method based on the given uncalibrated class probabilities X and ground truth labels y.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            Training data, i.e. predicted probabilities of the base classifier on the calibration set.
        y : array-like, shape (n_samples,)
            Target classes.
        n_jobs : int or None, optional (default=None)
            The number of jobs to use for the computation.
            ``None`` means 1 unless in a :obj:`joblib.parallel_backend` context.
            ``-1`` means using all processors. See :term:`Glossary <n_jobs>` for more details.
        Returns
        -------
        self : object
            Returns an instance of self.
        """
        if X.ndim == 1:
            raise ValueError("Calibration training data must have shape (n_samples, n_classes).")
        elif np.shape(X)[1] == 2:
            self.logistic_regressor_ = sklearn.linear_model.LogisticRegression(C=1 / self.regularization,
                                                                               solver='lbfgs',
                                                                               random_state=self.random_state)
            self.logistic_regressor_.fit(X[:, 1].reshape(-1, 1), y)
        elif np.shape(X)[1] > 2:
            self.onevsrest_calibrator_ = OneVsRestCalibrator(calibrator=clone(self), n_jobs=n_jobs)
            self.onevsrest_calibrator_.fit(X, y)

        return self

    def predict_proba(self, X):
        """
        Compute calibrated posterior probabilities for a given array of posterior probabilities from an arbitrary
        classifier.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            The uncalibrated posterior probabilities.
        Returns
        -------
        P : array, shape (n_samples, n_classes)
            The predicted probabilities.
        """
        if X.ndim == 1:
            raise ValueError("Calibration data must have shape (n_samples, n_classes).")
        elif np.shape(X)[1] == 2:
            check_is_fitted(self, "logistic_regressor_")
            return self.logistic_regressor_.predict_proba(X[:, 1].reshape(-1, 1))
        elif np.shape(X)[1] > 2:
            check_is_fitted(self, "onevsrest_calibrator_")
            return self.onevsrest_calibrator_.predict_proba(X)


class IsotonicRegression(CalibrationMethod):
    """
    Probability calibration using Isotonic Regression
    Isotonic regression [1]_ [2]_ is a non-parametric approach to mapping (non-probabilistic) classifier scores to
    probabilities. It assumes an isotonic (non-decreasing) relationship between classifier scores and probabilities.
    Parameters
    ----------
    out_of_bounds : string, optional, default: "clip"
        The ``out_of_bounds`` parameter handles how x-values outside of the
        training domain are handled.  When set to "nan", predicted y-values
        will be NaN.  When set to "clip", predicted y-values will be
        set to the value corresponding to the nearest train interval endpoint.
        When set to "raise", allow ``interp1d`` to throw ValueError.
    References
    ----------
    .. [1] Transforming Classifier Scores into Accurate Multiclass
           Probability Estimates, B. Zadrozny & C. Elkan, (KDD 2002)
    .. [2] Predicting Good Probabilities with Supervised Learning,
           A. Niculescu-Mizil & R. Caruana, ICML 2005
    """

    def __init__(self, out_of_bounds="clip"):
        super().__init__()
        self.out_of_bounds = out_of_bounds

    def fit(self, X, y, n_jobs=None):
        """
        Fit the calibration method based on the given uncalibrated class probabilities X and ground truth labels y.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            Training data, i.e. predicted probabilities of the base classifier on the calibration set.
        y : array-like, shape (n_samples,)
            Target classes.
        n_jobs : int or None, optional (default=None)
            The number of jobs to use for the computation.
            ``None`` means 1 unless in a :obj:`joblib.parallel_backend` context.
            ``-1`` means using all processors. See :term:`Glossary <n_jobs>` for more details.
        Returns
        -------
        self : object
            Returns an instance of self.
        """
        if X.ndim == 1:
            raise ValueError("Calibration training data must have shape (n_samples, n_classes).")
        elif np.shape(X)[1] == 2:
            self.isotonic_regressor_ = sklearn.isotonic.IsotonicRegression(increasing=True,
                                                                           out_of_bounds=self.out_of_bounds)
            self.isotonic_regressor_.fit(X[:, 1], y)
        elif np.shape(X)[1] > 2:
            self.onevsrest_calibrator_ = OneVsRestCalibrator(calibrator=clone(self), n_jobs=n_jobs)
            self.onevsrest_calibrator_.fit(X, y)
        return self

    def predict_proba(self, X):
        """
        Compute calibrated posterior probabilities for a given array of posterior probabilities from an arbitrary
        classifier.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            The uncalibrated posterior probabilities.
        Returns
        -------
        P : array, shape (n_samples, n_classes)
            The predicted probabilities.
        """
        if X.ndim == 1:
            raise ValueError("Calibration data must have shape (n_samples, n_classes).")
        elif np.shape(X)[1] == 2:
            check_is_fitted(self, "isotonic_regressor_")
            p1 = self.isotonic_regressor_.predict(X[:, 1])
            return np.column_stack([1 - p1, p1])
        elif np.shape(X)[1] > 2:
            check_is_fitted(self, "onevsrest_calibrator_")
            return self.onevsrest_calibrator_.predict_proba(X)


class HistogramBinning(CalibrationMethod):
    """

        Probability calibration using histogram binning
        Histogram binning [1]_ is a nonparametric approach to probability calibration. Classifier scores are binned into a
        given number of bins either based on fixed width or frequency. Classifier scores are then computed based on the
        empirical frequency of class 1 in each bin.

        Parameters
        ----------
            mode : str, default='equal_width'
                Binning mode used. One of ['equal_width', 'equal_freq'].
            n_bins : int, default=20
                Number of bins to bin classifier scores into.
            input_range : list, shape (2,), default=[0, 1]
                Range of the classifier scores.
        .. [1] Zadrozny, B. & Elkan, C. Obtaining calibrated probability estimates from decision trees and naive Bayesian
               classifiers in Proceedings of the 18th International Conference on Machine Learning (ICML, 2001), 609–616.
    """


    def __init__(self, mode='equal_width', n_bins=20, input_range=[0, 1]):
        super().__init__()
        if mode in ['equal_width', 'equal_freq']:
            self.mode = mode
        else:
            raise ValueError("Mode not recognized. Choose on of 'equal_width', or 'equal_freq'.")
        self.n_bins = n_bins
        self.input_range = input_range

    def fit(self, X, y, n_jobs=None):
        """
        Fit the calibration method based on the given uncalibrated class probabilities X and ground truth labels y.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            Training data, i.e. predicted probabilities of the base classifier on the calibration set.
        y : array-like, shape (n_samples,)
            Target classes.
        n_jobs : int or None, optional (default=None)
            The number of jobs to use for the computation.
            ``None`` means 1 unless in a :obj:`joblib.parallel_backend` context.
            ``-1`` means using all processors. See :term:`Glossary <n_jobs>` for more details.
        Returns
        -------
        self : object
            Returns an instance of self.
        """
        if X.ndim == 1:
            raise ValueError("Calibration training data must have shape (n_samples, n_classes).")
        elif np.shape(X)[1] == 2:
            return self._fit_binary(X, y)
        elif np.shape(X)[1] > 2:
            self.onevsrest_calibrator_ = OneVsRestCalibrator(calibrator=clone(self), n_jobs=n_jobs)
            self.onevsrest_calibrator_.fit(X, y)
        return self

    def _fit_binary(self, X, y):
        if self.mode == 'equal_width':
            # Compute probability of class 1 in each equal width bin
            binned_stat = scipy.stats.binned_statistic(x=X[:, 1], values=np.equal(1, y), statistic='mean',
                                                       bins=self.n_bins, range=self.input_range)
            self.prob_class_1 = binned_stat.statistic  # TODO: test this and correct attributes
            self.binning = binned_stat.bin_edges
        elif self.mode == 'equal_freq':
            # Find binning based on equal frequency
            self.binning = np.quantile(X[:, 1],
                                       q=np.linspace(self.input_range[0], self.input_range[1], self.n_bins + 1))

            # Compute probability of class 1 in equal frequency bins
            digitized = np.digitize(X[:, 1], bins=self.binning)
            digitized[digitized == len(self.binning)] = len(self.binning) - 1  # include rightmost edge in partition
            self.prob_class_1 = [y[digitized == i].mean() for i in range(1, len(self.binning))]

        return self

    def predict_proba(self, X):
        """
        Compute calibrated posterior probabilities for a given array of posterior probabilities from an arbitrary
        classifier.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            The uncalibrated posterior probabilities.
        Returns
        -------
        P : array, shape (n_samples, n_classes)
            The predicted probabilities.
        """
        if X.ndim == 1:
            raise ValueError("Calibration data must have shape (n_samples, n_classes).")
        elif np.shape(X)[1] == 2:
            check_is_fitted(self, ["binning", "prob_class_1"])
            # Find bin of predictions
            digitized = np.digitize(X[:, 1], bins=self.binning)
            digitized[digitized == len(self.binning)] = len(self.binning) - 1  # include rightmost edge in partition
            # Transform to empirical frequency of class 1 in each bin
            p1 = np.array([self.prob_class_1[j] for j in (digitized - 1)])
            # If empirical frequency is NaN, do not change prediction
            p1 = np.where(np.isfinite(p1), p1, X[:, 1])
            assert np.all(np.isfinite(p1)), "Predictions are not all finite."

            return np.column_stack([1 - p1, p1])
        elif np.shape(X)[1] > 2:
            check_is_fitted(self, "onevsrest_calibrator_")
            return self.onevsrest_calibrator_.predict_proba(X)


class BayesianBinningQuantiles(CalibrationMethod):
    """
    Probability calibration using Bayesian binning into quantiles
    Bayesian binning into quantiles [1]_ considers multiple equal frequency binning models and combines them through
    Bayesian model averaging. Each binning model :math:`M` is scored according to
    :math:`\\text{Score}(M) = P(M) \\cdot P(D | M),` where a uniform prior :math:`P(M)` is assumed. The marginal likelihood
    :math:`P(D | M)` has a closed form solution under the assumption of independent binomial class distributions in each
    bin with beta priors.
    Parameters
    ----------
        C : int, default = 10
            Constant controlling the number of binning models.
        input_range : list, shape (2,), default=[0, 1]
            Range of the scores to calibrate.
    .. [1] Naeini, M. P., Cooper, G. F. & Hauskrecht, M. Obtaining Well Calibrated Probabilities Using Bayesian Binning
           in Proceedings of the Twenty-Ninth AAAI Conference on Artificial Intelligence, Austin, Texas, USA.
    """

    def __init__(self, C=10, input_range=[0, 1]):
        super().__init__()
        self.C = C
        self.input_range = input_range

    def _binning_model_logscore(self, probs, y, partition, N_prime=2):
        """
        Compute the log score of a binning model
        Each binning model :math:`M` is scored according to :math:`Score(M) = P(M) \\cdot P(D | M),` where a uniform prior
        :math:`P(M)` is assumed and the marginal likelihood :math:`P(D | M)` has a closed form solution
        under the assumption of a binomial class distribution in each bin with beta priors.
        Parameters
        ----------
        probs : array-like, shape (n_samples, )
            Predicted posterior probabilities.
        y : array-like, shape (n_samples, )
            Target classes.
        partition : array-like, shape (n_bins + 1, )
            Interval partition defining a binning.
        N_prime : int, default=2
            Equivalent sample size expressing the strength of the belief in the prior distribution.
        Returns
        -------
        log_score : float
            Log of Bayesian score for a given binning model
        """
        # Setup
        B = len(partition) - 1
        p = (partition[1:] - partition[:-1]) / 2 + partition[:-1]

        # Compute positive and negative samples in given bins
        N = np.histogram(probs, bins=partition)[0]

        digitized = np.digitize(probs, bins=partition)
        digitized[digitized == len(partition)] = len(partition) - 1  # include rightmost edge in partition
        m = [y[digitized == i].sum() for i in range(1, len(partition))]
        n = N - m

        # Compute the parameters of the Beta priors
        tiny = np.finfo(np.float).tiny  # Avoid scipy.special.gammaln(0), which can arise if bin has zero width
        alpha = N_prime / B * p
        alpha[alpha == 0] = tiny
        beta = N_prime / B * (1 - p)
        beta[beta == 0] = tiny

        # Prior for a given binning model (uniform)
        log_prior = - np.log(self.T)

        # Compute the marginal log-likelihood for the given binning model
        log_likelihood = np.sum(
            scipy.special.gammaln(N_prime / B) + scipy.special.gammaln(m + alpha) + scipy.special.gammaln(n + beta) - (
                    scipy.special.gammaln(N + N_prime / B) + scipy.special.gammaln(alpha) + scipy.special.gammaln(
                beta)))

        # Compute score for the given binning model
        log_score = log_prior + log_likelihood
        return log_score

    def fit(self, X, y, n_jobs=None):
        """
        Fit the calibration method based on the given uncalibrated class probabilities X and ground truth labels y.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            Training data, i.e. predicted probabilities of the base classifier on the calibration set.
        y : array-like, shape (n_samples,)
            Target classes.
        n_jobs : int or None, optional (default=None)
            The number of jobs to use for the computation.
            ``None`` means 1 unless in a :obj:`joblib.parallel_backend` context.
            ``-1`` means using all processors. See :term:`Glossary <n_jobs>` for more details.
        Returns
        -------
        self : object
            Returns an instance of self.
        """
        if X.ndim == 1:
            raise ValueError("Calibration training data must have shape (n_samples, n_classes).")
        elif np.shape(X)[1] == 2:
            self.binnings = []
            self.log_scores = []
            self.prob_class_1 = []
            self.T = 0
            return self._fit_binary(X, y)
        elif np.shape(X)[1] > 2:
            self.onevsrest_calibrator_ = OneVsRestCalibrator(calibrator=clone(self), n_jobs=n_jobs)
            self.onevsrest_calibrator_.fit(X, y)
            return self

    def _fit_binary(self, X, y):
        # Determine number of bins
        N = len(y)
        min_bins = int(max(1, np.floor(N ** (1 / 3) / self.C)))
        max_bins = int(min(np.ceil(N / 5), np.ceil(self.C * N ** (1 / 3))))
        self.T = max_bins - min_bins + 1

        # Define (equal frequency) binning models and compute scores
        self.binnings = []
        self.log_scores = []
        self.prob_class_1 = []
        for i, n_bins in enumerate(range(min_bins, max_bins + 1)):
            # Compute binning from data and set outer edges to range
            binning_tmp = np.quantile(X[:, 1], q=np.linspace(self.input_range[0], self.input_range[1], n_bins + 1))
            binning_tmp[0] = self.input_range[0]
            binning_tmp[-1] = self.input_range[1]
            # Enforce monotonicity of binning (np.quantile does not guarantee monotonicity)
            self.binnings.append(np.maximum.accumulate(binning_tmp))
            # Compute score
            self.log_scores.append(self._binning_model_logscore(probs=X[:, 1], y=y, partition=self.binnings[i]))

            # Compute empirical accuracy for all bins
            digitized = np.digitize(X[:, 1], bins=self.binnings[i])
            # include rightmost edge in partition
            digitized[digitized == len(self.binnings[i])] = len(self.binnings[i]) - 1

            def empty_safe_bin_mean(a, empty_value):
                """
                Assign the bin mean to an empty bin. Corresponds to prior assumption of the underlying classifier
                being calibrated.
                """
                if a.size == 0:
                    return empty_value
                else:
                    return a.mean()

            self.prob_class_1.append(
                [empty_safe_bin_mean(y[digitized == k], empty_value=(self.binnings[i][k] + self.binnings[i][k - 1]) / 2)
                 for k in range(1, len(self.binnings[i]))])

        return self

    def predict_proba(self, X):
        """
        Compute calibrated posterior probabilities for a given array of posterior probabilities from an arbitrary
        classifier.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_classes)
            The uncalibrated posterior probabilities.
        Returns
        -------
        P : array, shape (n_samples, n_classes)
            The predicted probabilities.
        """
        if X.ndim == 1:
            raise ValueError("Calibration data must have shape (n_samples, n_classes).")
        elif np.shape(X)[1] == 2:
            check_is_fitted(self, ["binnings", "log_scores", "prob_class_1", "T"])

            # Find bin for all binnings and the associated empirical accuracy
            posterior_prob_binnings = np.zeros(shape=[np.shape(X)[0], len(self.binnings)])
            for i, binning in enumerate(self.binnings):
                bin_ids = np.searchsorted(binning, X[:, 1])
                bin_ids = np.clip(bin_ids, a_min=0, a_max=len(binning) - 1)  # necessary if X is out of range
                posterior_prob_binnings[:, i] = [self.prob_class_1[i][j] for j in (bin_ids - 1)]

            # Computed score-weighted average
            norm_weights = np.exp(np.array(self.log_scores) - scipy.special.logsumexp(self.log_scores))
            posterior_prob = np.sum(posterior_prob_binnings * norm_weights, axis=1)

            # Compute probability for other class
            return np.column_stack([1 - posterior_prob, posterior_prob])
        elif np.shape(X)[1] > 2:
            check_is_fitted(self, "onevsrest_calibrator_")
            return self.onevsrest_calibrator_.predict_proba(X)


CALIBRATION_MODELS = {
    'no_calibration': NoCalibration,
    'temperature_scaling': TemperatureScaling,
    'platt_scaling': PlattScaling,
    'isotonic_regression': IsotonicRegression,
    'histogram_binning': HistogramBinning,
    'bayesian_binning_quantiles': BayesianBinningQuantiles
}

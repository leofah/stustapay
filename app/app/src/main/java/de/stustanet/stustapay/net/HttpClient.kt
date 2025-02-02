package de.stustanet.stustapay.net

import android.util.Log
import io.ktor.client.*
import io.ktor.client.call.*
import io.ktor.client.engine.cio.*
import io.ktor.client.plugins.*
import io.ktor.client.plugins.contentnegotiation.*
import io.ktor.client.plugins.logging.*
import io.ktor.client.request.*
import io.ktor.client.statement.*
import io.ktor.http.*
import io.ktor.serialization.kotlinx.json.*
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

class HttpClientTarget(
    val url: String,
    val token: String,
)

@Serializable
data class ErrorDetail(
    val detail: String
)


class HttpClient(retry: Boolean = false, logRequests: Boolean = true, val targetConfig: suspend () -> HttpClientTarget?) {
    val httpClient = HttpClient(CIO) {

        // automatic json conversions
        install(ContentNegotiation) {
            json(Json {
                prettyPrint = true
                isLenient = true
                ignoreUnknownKeys = true
            })
        }

        if (retry) {
            install(HttpRequestRetry) {
                // retry for http500 errors
                retryOnServerErrors(maxRetries = 5)
                retryOnException(maxRetries = 3, retryOnTimeout = true)
                exponentialDelay()
            }
        }

        install(HttpTimeout) {
            connectTimeoutMillis = 400
            requestTimeoutMillis = 3000
            socketTimeoutMillis = 1000
        }

        if (logRequests) {
            install(Logging) {
                level = LogLevel.ALL
                logger = object : Logger {
                    override fun log(message: String) {
                        Log.d("SSP req", message)
                    }
                }
            }
        }

        // we use the transformRequest below for this.
        expectSuccess = false

        install(Logging)
    }


    suspend inline fun <reified T> transformResponse(response: HttpResponse): Response<T> {
        return when (response.status.value) {
            in 200..299 -> Response.OK(response.body())
            in 300..399 -> Response.Error.Msg("unhandled redirect")
            in 400..403 -> {
                Response.Error.Msg(
                    (response.body() as ErrorDetail).detail,
                    response.status.value
                )
            }
            else -> Response.Error.Msg("error ${response.status.value}: ${response.bodyAsText()}")
        }
    }

    /**
     * send a http get/post/... request using the current registration token.
     * @param basePath overrides api base path from registration, and if given, no bearer token is sent.
     */
    suspend inline fun <reified I, reified O> request(
        path: String,
        method: HttpMethod,
        options: HttpRequestBuilder.() -> Unit = {},
        basePath: String? = null,
        noinline body: (() -> I)? = null,
    ): Response<O> {

        try {
            Log.d("StuStaPay", "request ${method.value} to $path")

            val api: HttpClientTarget? = targetConfig()

            // prefer custom base-path over the one from the targetConfig.
            val reqBasePath = basePath ?: api?.url
            ?: return Response.Error.Msg("no api base path available")

            Log.d("StuStaPay", "basepath $reqBasePath/$path token: ${api?.token}")

            val response: HttpResponse = httpClient.request {
                this.method = method

                contentType(ContentType.Application.Json)

                url("${reqBasePath}/${path}")

                if (api != null) {
                    headers {
                        append(HttpHeaders.Authorization, "Bearer ${api.token}")
                    }
                }

                options()
                if (body != null) {
                    setBody(body())
                }
            }
            return transformResponse(response)
        } catch (e: Exception) {
            Log.e("StuStaPay", "request error: $path: ${e.localizedMessage}")
            return Response.Error.Exception(e)
        }
    }

    suspend inline fun <reified I, reified O> post(
        path: String,
        options: HttpRequestBuilder.() -> Unit = {},
        basePath: String? = null,
        noinline body: (() -> I)? = null
    ): Response<O> {
        return request(path, HttpMethod.Post, options, basePath, body)
    }

    suspend inline fun <reified O> get(
        path: String,
        options: HttpRequestBuilder.() -> Unit = {},
        basePath: String? = null,
    ): Response<O> {
        return request<Any, O>(path, HttpMethod.Get, options, basePath, null)
    }
}